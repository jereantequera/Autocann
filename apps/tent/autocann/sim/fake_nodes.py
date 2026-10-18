"""
Fake nodes: a sensor node posting like firmware/sensor_node, a relay node polling
like firmware/relay_node, and the tent model they share.
"""

from __future__ import annotations

import dataclasses
import random
import threading
import time
from typing import Any, Dict, Mapping, Optional

from autocann.hardware.outputs import OUTPUT_NAMES
from autocann.sim import nodes
from autocann.sim.http_client import http_json
from autocann.sim.model import TentModel
from autocann.sim.nodes import NodeStatus, attempt, build_sensor_payload
from autocann.sim.scenarios import initial_model, scenario_readings

#: The fake relay node polls every 2 s, as firmware/relay_node does.
RELAY_POLL_SECONDS = 2.0
#: The firmware relay node switches everything off if it cannot reach the Pi
#: for this long (WATCHDOG_MS in firmware/relay_node).
RELAY_WATCHDOG_SECONDS = 60.0


def apply_desired(payload: Mapping[str, Any]) -> Dict[str, bool]:
    """The outputs a relay node switches to for a `/api/outputs/desired` answer; failsafe or unnamed means off."""
    if payload.get("failsafe"):
        return {name: False for name in OUTPUT_NAMES}
    outputs = payload.get("outputs") or {}
    return {name: bool(outputs.get(name, False)) for name in OUTPUT_NAMES}


class TentWorld:
    """The tent model and the outputs the fake relay node applied, shared between threads."""

    def __init__(self, scenario: str) -> None:
        self._lock = threading.Lock()
        self._model = initial_model(scenario)
        self._outputs: Dict[str, bool] = {name: False for name in OUTPUT_NAMES}

    def advance(self, wall_seconds: float, speed: float) -> None:
        with self._lock:
            self._model.step(wall_seconds * speed, self._outputs)

    def snapshot(self) -> TentModel:
        with self._lock:
            return dataclasses.replace(self._model)

    def outputs(self) -> Dict[str, bool]:
        with self._lock:
            return dict(self._outputs)

    def set_outputs(self, outputs: Mapping[str, bool]) -> None:
        with self._lock:
            self._outputs = dict(outputs)


class FakeSensorNode(threading.Thread):
    """Posts readings and its own status the way firmware/sensor_node does, every `interval` s."""

    def __init__(self, base_url: str, scenario: str, world: TentWorld, interval: float,
                 halt: threading.Event, rng: random.Random) -> None:
        super().__init__(name="fake-sensor-node", daemon=True)
        self._base_url = base_url
        self._scenario = scenario
        self._world = world
        self._interval = interval
        self._halt = halt
        self._rng = rng
        self._silent_reported = False
        dropouts = nodes.DROPOUT_CHANCE if scenario in ("drift", "spike") else 0.0
        self._status = NodeStatus(nodes.SENSOR_NODE_NAME, ip=nodes.SENSOR_NODE_IP, sensors=nodes.SENSOR_PINS,
                                  firmware=nodes.FW_VERSION, rng=rng, dropout_chance=dropouts)

    def run(self) -> None:
        print(nodes.WIFI_CONNECTING_SERIAL_LINE)
        self._status.boot_log()
        started = time.monotonic()
        prev_t: Optional[float] = None
        while not self._halt.is_set():
            t = time.monotonic() - started
            readings = scenario_readings(self._scenario, t, self._world.snapshot(), prev_t, self._rng)
            prev_t = t
            if readings is None:
                if not self._silent_reported:
                    self._silent_reported = True
                    print(f"[sensors] silent from now on ({self._scenario} scenario)")
            for reading in readings or ():
                if self._status.read(reading.sensor_id, reading.temperature, reading.humidity):
                    code = attempt(lambda r=reading: http_json(
                        "POST", f"{self._base_url}/api/sensor/indoor", build_sensor_payload(r)))
                    self._status.posted(reading.sensor_id, code)
            report = self._status.due_report()
            if report is not None:
                self._status.report_sent(attempt(lambda r=report: http_json(
                    "POST", f"{self._base_url}/api/node/log", r)))
            self._halt.wait(self._interval)


class FakeRelayNode(threading.Thread):
    """Polls the desired outputs the way firmware/relay_node does and applies them to the model."""

    def __init__(self, base_url: str, world: TentWorld, halt: threading.Event) -> None:
        super().__init__(name="fake-relay-node", daemon=True)
        self._base_url = base_url
        self._world = world
        self._halt = halt

    def run(self) -> None:
        last_ok = time.monotonic()
        last_failsafe: Optional[bool] = None
        while not self._halt.is_set():
            try:
                payload = http_json("GET", f"{self._base_url}/api/outputs/desired")
                last_ok = time.monotonic()
                self._world.set_outputs(apply_desired(payload))
                failsafe = bool(payload.get("failsafe"))
                if failsafe != last_failsafe:
                    last_failsafe = failsafe
                    reason = payload.get("reason", "")
                    print(f"[relay] {'failsafe: all off' if failsafe else 'following the loop'}"
                          f"{' (' + reason + ')' if reason else ''}")
            except (OSError, ValueError) as e:
                print(f"[relay] poll failed: {e}")
                if time.monotonic() - last_ok > RELAY_WATCHDOG_SECONDS:
                    self._world.set_outputs({name: False for name in OUTPUT_NAMES})
            self._halt.wait(RELAY_POLL_SECONDS)
