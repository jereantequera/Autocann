"""
Tent simulator: the whole stack, with fake sensors and relays.

Starts a sandboxed backend and control loop, then drives them with a fake
sensor node (posting like firmware/sensor_node) and a fake relay node (polling
like firmware/relay_node) against a small model of the tent. Run it with
`make sim` from apps/tent.

Isolation is the point: the machine this runs on may be driving a real tent.
Every child process gets a non-zero Redis DB, a fresh SQLite file, remote relay
mode (never GPIO) and a free 127.0.0.1 port, and only the PIDs this module
started are ever signalled.

The tent model and the scenarios are pure and unit-tested; I/O sits below the
"Sandbox" and "Run" sections. Synthetic history lives in autocann/sim/.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from autocann.config import RELAY_MODE_REMOTE, redis_config_from_env
from autocann.control.vpd_math import calculate_vpd
from autocann.hardware.outputs import OUTPUT_NAMES
from autocann.paths import DATA_DIR, PROJECT_ROOT
from autocann.sim import nodes
from autocann.sim.nodes import NodeStatus, Reading, attempt, build_sensor_payload
from autocann.sim.seed import seed_sandbox_history

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Redis DB the sandbox uses unless told otherwise.
DEFAULT_REDIS_DB = 15
#: The real tent's Redis DB. Refused for the sandbox, always.
REAL_TENT_REDIS_DB = 0
#: Redis databases run 0 to 15 by default.
MAX_REDIS_DB = 15

#: Ports the real tent's backend uses (5000, and 5055 on the same Pi). Refused.
RESERVED_PORTS = frozenset({5000, 5055})

DEFAULT_INTERVAL_SECONDS = 5.0
REPORT_EVERY_SECONDS = 10.0
TICK_SECONDS = 0.5
#: The fake relay node polls every 2 s, as firmware/relay_node does.
RELAY_POLL_SECONDS = 2.0
#: The firmware relay node switches everything off if it cannot reach the Pi
#: for this long (WATCHDOG_MS in firmware/relay_node).
RELAY_WATCHDOG_SECONDS = 60.0

#: Outdoor air, which the tent drifts toward when nothing is switching it.
OUTDOOR_TEMP_C = 20.0
OUTDOOR_RH = 50.0
#: Exponential time constants, in model seconds.
BASE_TEMP_TAU_SECONDS = 7200.0
BASE_RH_TAU_SECONDS = 3600.0
#: With the extractor on, the tent exchanges air with the outside much faster.
VENTILATION_TAU_SECONDS = 900.0
#: Humidity change rates while a device is on, in %RH per model second.
HUMIDIFIER_RH_PER_SECOND = 0.02
DEHUMIDIFIER_RH_PER_SECOND = 0.02

#: Sensor noise, half-width, in the sensor's own unit.
SENSOR_NOISE = 0.1
#: Sensor "b" sits above the lamp in the real tent: warmer and drier.
OBSERVER_TEMP_OFFSET_C = 1.5
OBSERVER_RH_OFFSET = -3.0
#: The witness drifts away from the primary at this rate, in model time.
DRIFT_RATE_C_PER_HOUR = 2.0
DRIFT_MAX_C = 3.0

#: Wall-clock seconds into the run at which the sensors go silent (`stale`).
STALE_AFTER_SECONDS = 60.0
#: Wall-clock seconds into the run at which one reading is an absurd outlier.
SPIKE_AT_SECONDS = 60.0
SPIKE_TEMP_DELTA_C = 25.0

PRIMARY_SENSOR_ID = "a"
OBSERVER_SENSOR_ID = "b"

#: Sent to the child control loop before it imports anything: hardware modules
#: are blocked, so the loop cannot reach GPIO or a DHT22 of the real tent.
LOOP_BOOTSTRAP = (
    "import sys\n"
    "for _name in ('gpiozero', 'RPi', 'board', 'adafruit_dht'):\n"
    "    sys.modules[_name] = None\n"
    "from autocann.cli.vpd import main\n"
    "raise SystemExit(main(stage_override=None, use_esp32_indoor=True))\n"
)

PR_SET_PDEATHSIG = 1


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    start_rh: float
    start_temp_c: float = 24.0


SCENARIOS: Dict[str, Scenario] = {
    s.name: s
    for s in (
        Scenario(
            "normal",
            "starts a little dry of the band; the loop should humidify into it",
            start_rh=54.0,
        ),
        Scenario(
            "humid",
            "starts far too humid; the loop should run the dehumidifier and extractor",
            start_rh=88.0,
        ),
        Scenario(
            "dry",
            "starts far too dry; the loop should run the humidifier",
            start_rh=38.0,
        ),
        Scenario(
            "stale",
            f"sensors go silent after {STALE_AFTER_SECONDS:.0f} s; the dashboard "
            "should show it and the loop should fail safe",
            start_rh=54.0,
        ),
        Scenario(
            "spike",
            f"one absurd reading ({SPIKE_TEMP_DELTA_C:+.0f} °C) at "
            f"{SPIKE_AT_SECONDS:.0f} s; the median filter should ignore it",
            start_rh=54.0,
        ),
        Scenario(
            "drift",
            "two sensors: witness 'b' drifts away from 'a'; the drift detector "
            "should flag it",
            start_rh=54.0,
        ),
        Scenario(
            "offline-relay",
            "the relay node never polls; the loop asks for outputs the tent never sees",
            start_rh=54.0,
        ),
    )
}


# ---------------------------------------------------------------------------
# Tent model (pure)
# ---------------------------------------------------------------------------


def _relax(value: float, target: float, dt: float, tau: float) -> float:
    """Move `value` toward `target` over `dt`, exactly, for any `dt`."""
    return target + (value - target) * math.exp(-dt / tau)


def _clamp_rh(rh: float) -> float:
    return min(100.0, max(0.0, rh))


@dataclass
class TentModel:
    """
    Air temperature and relative humidity of the tent, in model time.

    The air relaxes toward the outdoor air; the humidifier and dehumidifier add
    and remove moisture at a constant rate; the extractor speeds the exchange.
    """

    temp: float
    rh: float
    seconds: float = 0.0

    def step(self, dt: float, outputs: Mapping[str, bool]) -> Tuple[float, float]:
        """Advance by `dt` model seconds with the given outputs on or off."""
        if dt <= 0:
            return self.temp, self.rh
        ventilating = bool(outputs.get("ventilation"))
        temp_tau = VENTILATION_TAU_SECONDS if ventilating else BASE_TEMP_TAU_SECONDS
        rh_tau = VENTILATION_TAU_SECONDS if ventilating else BASE_RH_TAU_SECONDS

        self.temp = _relax(self.temp, OUTDOOR_TEMP_C, dt, temp_tau)
        self.rh = _relax(self.rh, OUTDOOR_RH, dt, rh_tau)
        if outputs.get("humidity_up"):
            self.rh += HUMIDIFIER_RH_PER_SECOND * dt
        if outputs.get("humidity_down"):
            self.rh -= DEHUMIDIFIER_RH_PER_SECOND * dt
        self.rh = _clamp_rh(self.rh)
        self.seconds += dt
        return self.temp, self.rh


def initial_model(scenario: str) -> TentModel:
    spec = scenario_spec(scenario)
    return TentModel(temp=spec.start_temp_c, rh=spec.start_rh)


def scenario_spec(name: str) -> Scenario:
    try:
        return SCENARIOS[name]
    except KeyError:
        raise ValueError(f"unknown scenario {name!r}; choose from {', '.join(SCENARIOS)}") from None


def witness_offset_c(model_seconds: float) -> float:
    """How far the witness has drifted from the primary after `model_seconds`."""
    hours = model_seconds / 3600.0
    return min(DRIFT_MAX_C, DRIFT_RATE_C_PER_HOUR * hours)


def _jitter(value: float, rng: Optional[random.Random]) -> float:
    return value if rng is None else value + rng.uniform(-SENSOR_NOISE, SENSOR_NOISE)


def scenario_readings(
    name: str,
    t: float,
    model: TentModel,
    prev_t: Optional[float] = None,
    rng: Optional[random.Random] = None,
) -> Optional[List[Reading]]:
    """
    What the sensor nodes post at wall-clock second `t` of the run.

    Returns None when the sensors are silent (`stale`). The spike goes out with
    the first post at or after `SPIKE_AT_SECONDS`; `rng` adds noise, and without
    it readings are exact, as the tests need.
    """
    scenario_spec(name)  # refuses an unknown scenario
    if name == "stale" and t >= STALE_AFTER_SECONDS:
        return None

    temp = _jitter(model.temp, rng)
    rh = _clamp_rh(_jitter(model.rh, rng))
    if name == "spike" and t >= SPIKE_AT_SECONDS and (prev_t is None or prev_t < SPIKE_AT_SECONDS):
        temp += SPIKE_TEMP_DELTA_C

    readings = [Reading(PRIMARY_SENSOR_ID, temp, rh)]
    if name == "drift":
        witness_temp = model.temp + witness_offset_c(model.seconds)
        readings.append(Reading(
            OBSERVER_SENSOR_ID,
            _jitter(witness_temp, rng),
            _clamp_rh(_jitter(model.rh, rng)),
        ))
    else:
        readings.append(Reading(
            OBSERVER_SENSOR_ID,
            _jitter(model.temp + OBSERVER_TEMP_OFFSET_C, rng),
            _clamp_rh(_jitter(model.rh + OBSERVER_RH_OFFSET, rng)),
        ))
    return readings


def indoor_sensor_env(scenario: str) -> Dict[str, str]:
    """The indoor sensor roles the control loop should be started with."""
    if scenario == "drift":
        return {"AUTOCANN_INDOOR_PRIMARY": PRIMARY_SENSOR_ID,
                "AUTOCANN_INDOOR_WITNESS": OBSERVER_SENSOR_ID}
    return {"AUTOCANN_INDOOR_PRIMARY": PRIMARY_SENSOR_ID,
            "AUTOCANN_INDOOR_OBSERVERS": OBSERVER_SENSOR_ID}


def relay_polls(scenario: str) -> bool:
    """Whether the fake relay node polls at all in this scenario."""
    return scenario != "offline-relay"


def apply_desired(payload: Mapping[str, Any]) -> Dict[str, bool]:
    """The outputs a relay node switches to for a `/api/outputs/desired` answer; failsafe or unnamed means off."""
    if payload.get("failsafe"):
        return {name: False for name in OUTPUT_NAMES}
    outputs = payload.get("outputs") or {}
    return {name: bool(outputs.get(name, False)) for name in OUTPUT_NAMES}


def status_line(elapsed: float, model: TentModel, outputs: Mapping[str, bool]) -> str:
    minutes, seconds = divmod(int(elapsed), 60)
    on = [name for name in OUTPUT_NAMES if outputs.get(name)]
    return (
        f"[{minutes:02d}:{seconds:02d}] T={model.temp:.1f}°C RH={model.rh:.1f}% "
        f"VPD={calculate_vpd(model.temp, model.rh):.2f} kPa  "
        f"on: {', '.join(on) if on else 'none'}"
    )


# ---------------------------------------------------------------------------
# Sandbox guards and environment (pure)
# ---------------------------------------------------------------------------


class SandboxError(ValueError):
    """A setting that would let the simulator reach the real tent."""


def check_redis_db(db: int) -> int:
    if db == REAL_TENT_REDIS_DB:
        raise SandboxError(
            "refusing Redis DB 0: that is the real tent's. The simulator uses "
            f"DB {DEFAULT_REDIS_DB} by default"
        )
    if not 1 <= db <= MAX_REDIS_DB:
        raise SandboxError(f"Redis DB {db} is out of range (1 to {MAX_REDIS_DB})")
    return db


def check_port(port: int) -> int:
    if port in RESERVED_PORTS:
        raise SandboxError(f"refusing port {port}: the real tent's backend uses it")
    if not 1024 <= port <= 65535:
        raise SandboxError(f"port {port} is out of range")
    return port


def check_db_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    data_dir = DATA_DIR.resolve()
    if data_dir == resolved or data_dir in resolved.parents:
        raise SandboxError(f"refusing {resolved}: it is inside the real data directory")
    if resolved.exists():
        raise SandboxError(f"{resolved} already exists; the simulator needs a fresh file")
    return resolved


def free_port() -> int:
    """A port the OS says is free on 127.0.0.1, never a reserved one."""
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if port not in RESERVED_PORTS:
            return port


def build_child_env(
    *,
    redis_db: int,
    db_path: Path,
    port: int,
    scenario: str,
    base: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """
    Environment for the backend and the control loop.

    The relay mode is forced to remote, the Redis DB and SQLite file are the
    sandbox's own, and indoor sensor roles come from the scenario alone.
    """
    env = dict(os.environ if base is None else base)
    env.pop("PORT", None)
    for key in ("AUTOCANN_INDOOR_PRIMARY", "AUTOCANN_INDOOR_WITNESS", "AUTOCANN_INDOOR_OBSERVERS"):
        env.pop(key, None)
    env.update({
        "AUTOCANN_REDIS_DB": str(check_redis_db(redis_db)),
        "AUTOCANN_DB": str(db_path),
        "AUTOCANN_RELAY_MODE": RELAY_MODE_REMOTE,
        "AUTOCANN_WEB_HOST": "127.0.0.1",
        "AUTOCANN_WEB_PORT": str(check_port(port)),
        "AUTOCANN_WEB_DEBUG": "0",
        "PYTHONPATH": str(PROJECT_ROOT),
        "PYTHONUNBUFFERED": "1",
    })
    env.update(indoor_sensor_env(scenario))
    return env


# ---------------------------------------------------------------------------
# Sandbox: child processes and HTTP (I/O)
# ---------------------------------------------------------------------------


class SimulatorError(RuntimeError):
    """Something the run could not recover from. The sandbox is torn down."""


def _die_with_parent() -> None:
    """Linux only: the child gets SIGTERM if the simulator dies without cleaning up."""
    try:
        import ctypes

        ctypes.CDLL(None).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except (OSError, AttributeError):
        pass


def start_child(command: Sequence[str], env: Mapping[str, str], stdout: Any = None) -> subprocess.Popen:
    """Start one child and keep its handle. Its own session: a terminal Ctrl-C does not reach it."""
    preexec = _die_with_parent if sys.platform.startswith("linux") else None
    return subprocess.Popen(
        list(command),
        cwd=str(PROJECT_ROOT),
        env=dict(env),
        stdout=stdout,
        stderr=subprocess.STDOUT if stdout is not None else None,
        start_new_session=True,
        preexec_fn=preexec,
    )


def stop_children(children: Sequence[Tuple[str, subprocess.Popen]], grace_seconds: float = 15.0) -> None:
    """
    Stop exactly these processes: SIGTERM, wait, SIGKILL after a timeout. Nothing is found by name.
    """
    for _, proc in children:
        if proc.poll() is None:
            proc.terminate()
    for label, proc in children:
        try:
            proc.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            print(f"{label} did not stop in {grace_seconds:.0f} s; killing pid {proc.pid}")
            proc.kill()
            proc.wait(timeout=5)


def http_json(method: str, url: str, body: Optional[Mapping[str, Any]] = None, timeout: float = 3.0) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode() or "null")


# ---------------------------------------------------------------------------
# Fake devices
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def _wait_for_backend(base_url: str, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SimulatorError(f"backend exited with code {proc.returncode}; see its log")
        try:
            http_json("GET", f"{base_url}/api/config", timeout=2.0)
            return
        except (OSError, ValueError):
            time.sleep(0.5)
    raise SimulatorError("backend did not answer in time")


def _create_grow(base_url: str, scenario: str) -> None:
    result = http_json("POST", f"{base_url}/api/grows",
                       {"name": f"Simulated grow ({scenario})", "stage": "early_veg"})
    if not result.get("success"):
        raise SimulatorError(f"could not create the simulated grow: {result}")


def _reset_redis(redis_db: int) -> None:
    """Empty the sandbox's own Redis DB, so no key from an earlier run can pause or fail-safe this one."""
    import redis

    cfg = redis_config_from_env()
    redis.Redis(host=cfg.host, port=cfg.port, db=redis_db, socket_timeout=3).flushdb()


def run(args: argparse.Namespace) -> int:
    redis_db = check_redis_db(args.redis_db)
    scenario = args.scenario
    scenario_spec(scenario)
    # Validated before anything is created, so a refused path leaves nothing behind.
    requested_db = check_db_path(Path(args.db)) if args.db else None

    stop = threading.Event()
    children: List[Tuple[str, subprocess.Popen]] = []
    threads: List[threading.Thread] = []
    log_file = None
    previous_handlers = {}
    workdir = Path(tempfile.mkdtemp(prefix="autocann-sim-"))

    def request_stop(signum, _frame) -> None:
        print(f"\nSignal {signum} received, stopping the simulator...")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[sig] = signal.signal(sig, request_stop)

    exit_code = 0
    try:
        db_path = requested_db or workdir / "autocann-sim.db"
        port = check_port(free_port())
        base_url = f"http://127.0.0.1:{port}"
        env = build_child_env(redis_db=redis_db, db_path=db_path, port=port, scenario=scenario)

        _reset_redis(redis_db)
        print(f"Scenario: {scenario} - {SCENARIOS[scenario].description}")
        print(f"Sandbox: Redis DB {redis_db}, SQLite {db_path}, relay mode remote, port {port}")

        log_file = open(workdir / "backend.log", "w", encoding="utf-8")
        backend = start_child([sys.executable, "-m", "autocann.cli.backend"], env, stdout=log_file)
        children.append(("backend", backend))
        _wait_for_backend(base_url, backend)
        _create_grow(base_url, scenario)
        if args.history:
            try:
                print(seed_sandbox_history(db_path, args.history, with_gaps=args.history_gaps,
                                           now=int(time.time()), sandbox_dir=None if requested_db else workdir))
            except ValueError as e:
                raise SimulatorError(f"could not seed the history: {e}") from None

        loop = start_child([sys.executable, "-c", LOOP_BOOTSTRAP], env)
        children.append(("control loop", loop))

        world = TentWorld(scenario)
        sensor_node = FakeSensorNode(base_url, scenario, world, args.interval, stop, random.Random())
        threads.append(sensor_node)
        if relay_polls(scenario):
            threads.append(FakeRelayNode(base_url, world, stop))
        for thread in threads:
            thread.start()

        print(f"Dashboard: {base_url}/  (open it while the simulator runs)")
        print("Press Ctrl-C to stop.")
        _run_until_done(stop, children, world, args)
    except (SimulatorError, OSError) as e:
        print(f"Simulator error: {e}", file=sys.stderr)
        exit_code = 1
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=5)
        stop_children(children)
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        if log_file is not None:
            log_file.close()
        shutil.rmtree(workdir, ignore_errors=True)
        print("Simulator stopped; its processes and listener are gone.")
    return exit_code


def _run_until_done(stop: threading.Event, children: Sequence[Tuple[str, subprocess.Popen]],
                    world: TentWorld, args: argparse.Namespace) -> None:
    started = time.monotonic()
    last_tick = started
    last_report = started - REPORT_EVERY_SECONDS
    while not stop.is_set():
        now = time.monotonic()
        world.advance(now - last_tick, args.speed)
        last_tick = now
        if args.duration is not None and now - started >= args.duration:
            print("Duration reached.")
            return
        for label, proc in children:
            if proc.poll() is not None:
                raise SimulatorError(f"{label} exited with code {proc.returncode}")
        if now - last_report >= REPORT_EVERY_SECONDS:
            last_report = now
            print(status_line(now - started, world.snapshot(), world.outputs()), flush=True)
        stop.wait(TICK_SECONDS)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _scenario_help() -> str:
    lines = ["scenarios:"]
    for spec in SCENARIOS.values():
        lines.append(f"  {spec.name:<14} {spec.description}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m autocann.cli.simulate",
        description="Run the backend, the control loop and fake sensor and relay nodes "
                    "in an isolated sandbox.",
        epilog=_scenario_help(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--scenario", choices=list(SCENARIOS), default="normal",
                        help="what to simulate (default: normal)")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="model acceleration: model seconds per wall second (default: 1)")
    parser.add_argument("--duration", type=float, default=None,
                        help="stop after this many wall seconds (default: until Ctrl-C)")
    parser.add_argument("--redis-db", type=int, default=DEFAULT_REDIS_DB,
                        help=f"Redis DB for the sandbox; 0 is refused (default: {DEFAULT_REDIS_DB})")
    parser.add_argument("--db", default=None,
                        help="SQLite file to create; must not exist (default: a temp file)")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS,
                        help="seconds between sensor posts (default: 5)")
    parser.add_argument("--history", type=int, default=0, metavar="DAYS",
                        help="seed DAYS of synthetic history into the grow first (default: 0)")
    parser.add_argument("--history-gaps", action="store_true",
                        help="with --history, drop two 2-hour holes from it")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.speed <= 0:
        parser.error("--speed must be positive")
    if args.duration is not None and args.duration <= 0:
        parser.error("--duration must be positive")
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if args.history < 0:
        parser.error("--history must not be negative")
    if args.history_gaps and not args.history:
        parser.error("--history-gaps needs --history")
    try:
        check_redis_db(args.redis_db)
    except SandboxError as e:
        parser.error(str(e))
    try:
        return run(args)
    except SandboxError as e:
        parser.error(str(e))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
