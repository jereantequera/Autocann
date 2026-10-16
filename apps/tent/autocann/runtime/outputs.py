"""
Relay drivers for the humidity outputs, and the operator inputs that drive them: the test-mode
output requests and the manual overrides published by the web API.
"""

from __future__ import annotations

import json
from time import monotonic
from typing import Any, Dict, Optional

from autocann.config import (
    DESIRED_OUTPUTS_KEY,
    RELAY_MODE_GPIO,
    RELAY_MODE_REMOTE,
    TEST_MODE_OUTPUTS_KEY,
    ControlTuning,
)
from autocann.control.humidity import (
    DEHUMIDIFY,
    HUMIDIFY,
)
from autocann.db import store_control_event
from autocann.hardware.outputs import (
    get_outputs,
    manual_override_key,
    parse_bool_flag,
    pin_sources,
)
from autocann.runtime.clock import _now_local
from autocann.runtime.redis import _set_redis, redis_client


class Relays:
    """
    Owns the humidity output pins.

    This process is the only GPIO owner: gpiozero raises GPIOPinInUse if a
    second process claims the same pin, and a pin released by `close()` reverts
    to its default state, which silently drops the relay.
    """

    def __init__(self, mode: str = RELAY_MODE_GPIO) -> None:
        #: RELAY_MODE_GPIO drives local pins; RELAY_MODE_REMOTE only publishes
        #: the desired state for a node that polls for it.
        self.mode = mode
        self._specs = {o["name"]: o for o in get_outputs()}
        self._devices: Dict[str, Any] = {name: None for name in self._specs}
        self._state: Dict[str, bool] = {name: False for name in self._specs}
        #: Whether transitions reach the event log at all. Test mode turns it
        #: off for the whole mode rather than at each call site: the paths that
        #: switch a relay without being asked to — the shutdown handler, the
        #: catch-all in the loop — are exactly the ones that would forget, and
        #: a bench session leaving one row per shutdown in the history is the
        #: promise of "nothing is stored" quietly broken.
        self.log_events = True
        #: Monotonic time of each output's last real transition. Unset means it
        #: has not switched in this process's lifetime, so nothing is waiting.
        #: Used only by the manual path: the automatic one gets its minimum
        #: on/off times from the controller's own state machine.
        self._changed_at: Dict[str, float] = {}

    def setup(self) -> None:
        if self.mode == RELAY_MODE_REMOTE:
            # The node's own GPIO, which is the number the dashboard shows too:
            # this board's header is not in the circuit at all.
            sources = pin_sources(self.mode)
            for name, spec in self._specs.items():
                source = sources.get(name) or {}
                print(f"📡 {spec['label']} → {source.get('node') or 'nodo remoto'} "
                      f"GPIO {source.get('number')}")
            self.all_off(log_event=False)
            self.publish_outputs()
            return

        import gpiozero  # imported lazily so this module stays importable off-Pi

        for name, spec in self._specs.items():
            self._devices[name] = gpiozero.OutputDevice(
                int(spec["pin_bcm"]),
                active_high=bool(spec.get("active_high", True)),
                initial_value=False,
            )
            print(f"🔌 {spec['label']} → BCM {spec['pin_bcm']}")
        self.all_off(log_event=False)
        self.publish_outputs()

    def apply(self, action: str, *, overrides: Optional[Dict[str, bool]] = None,
              log_event: bool = True) -> None:
        """
        Drive every output to the state implied by `action`, with `overrides`
        taking precedence.

        The desired state is resolved first and written once. Applying the
        automatic action and then the overrides on top would toggle an overridden
        relay off and on again on every single iteration.
        """
        desired = {name: False for name in self._specs}
        desired["humidity_up"] = action == HUMIDIFY
        desired["humidity_down"] = action == DEHUMIDIFY

        for name, forced in (overrides or {}).items():
            if name in desired:
                desired[name] = forced

        for name, on in desired.items():
            self.set_output(name, on, log_event=log_event)
        self.publish_state()

    def all_off(self, *, log_event: bool = True) -> None:
        """Unconditionally de-energise everything. Overrides do not apply here."""
        for name in self._specs:
            self.set_output(name, False, log_event=log_event)
        self.publish_state()

    def publish_outputs(self) -> None:
        """
        Write every output's state to Redis whether or not it just changed.

        `set_output` short-circuits when nothing changed, which is right for the
        pin and for the event log, but it means an output that has never
        switched has no key at all. The dashboard reads a missing key as
        "unknown" and draws a dash over a relay that is known to be off — which
        is most visible in test mode, where someone is looking straight at the
        cards waiting for one to move.
        """
        for name, spec in self._specs.items():
            redis_key = spec.get("redis_key")
            if redis_key:
                _set_redis(redis_key, "true" if self._state[name] else "false")

    def publish_state(self) -> None:
        """
        Publish the whole desired state, timestamped, on every cycle.

        Deliberately uncached: `set_output` short-circuits when nothing changed,
        which is right for logging and for a local pin, but a remote node needs
        to hear from the loop on every cycle. Two reasons:

        - the poll is also the keepalive, so a silent cycle reads as "the Pi is
          gone" and the node de-energises;
        - if the node ever drops the relays on its own, the loop's cached state
          would still say "on" and it would never re-send the command. Sending
          the full state every time makes that divergence impossible.
        """
        payload = {
            "outputs": dict(self._state),
            "ts": int(_now_local().timestamp()),
        }
        _set_redis(DESIRED_OUTPUTS_KEY, json.dumps(payload))

    def set_output(self, name: str, on: bool, *, log_event: bool = True,
                   now: Optional[float] = None) -> None:
        """
        `now` is the monotonic reading to stamp the transition with. It is a
        parameter rather than read here so that a caller working from one clock
        reading — the loop does, and so do the tests — records the same instant
        it is reasoning about.
        """
        spec = self._specs.get(name)
        if spec is None or self._state.get(name) == on:
            return

        if self.mode == RELAY_MODE_GPIO:
            device = self._devices.get(name)
            try:
                if device is not None:
                    if on:
                        device.on()
                    else:
                        device.off()
            except Exception as e:
                print(f"❌ Failed to switch {name}: {e}")
                return

        self._state[name] = on
        self._changed_at[name] = monotonic() if now is None else now
        redis_key = spec.get("redis_key")
        if redis_key:
            _set_redis(redis_key, "true" if on else "false")
        if log_event and self.log_events:
            # Only transitions are recorded. The old loop logged an event on
            # every iteration, which added ~29k rows per day.
            store_control_event(name, "on" if on else "off")

    def hold_remaining(self, name: str, tuning: ControlTuning, now: float) -> float:
        """
        Seconds this output must stay as it is before it may switch again.

        An output that is on owes its minimum *on* time; one that is off owes
        its minimum *off* time.
        """
        changed_at = self._changed_at.get(name)
        if changed_at is None:
            return 0.0
        minimum = tuning.min_on_seconds if self._state.get(name) else tuning.min_off_seconds
        return max(0.0, minimum - (now - changed_at))

    def apply_manual(self, desired: Dict[str, bool], *, tuning: ControlTuning,
                     now: float) -> Dict[str, float]:
        """
        Drive the outputs to exactly what was asked, with nothing deciding
        behind them. Returns the outputs held back, with the seconds left.

        Two things this does not do, both deliberate:

        - **It logs no control events.** Test mode writes nothing to the
          database; a bench session toggling relays by hand is not history.
        - **It does not waive the minimum on/off times.** Those protect the
          equipment, and the equipment does not care whether the command came
          from a person or from the controller. A command that has to wait is
          *reported*, not dropped, so the dashboard can say why a relay did not
          move instead of looking broken.
        """
        held: Dict[str, float] = {}
        for name in self._specs:
            want = bool(desired.get(name, False))
            if want != self._state.get(name, False):
                remaining = self.hold_remaining(name, tuning, now)
                if remaining > 0:
                    held[name] = round(remaining, 1)
                    continue
            self.set_output(name, want, log_event=False, now=now)
        self.publish_state()
        return held

    def close(self) -> None:
        for device in self._devices.values():
            try:
                if device is not None:
                    device.off()
                    device.close()
            except Exception:
                pass


def read_test_mode_outputs() -> Dict[str, bool]:
    """What the operator last asked each output to do in test mode."""
    try:
        raw = redis_client.get(TEST_MODE_OUTPUTS_KEY)
        requested = json.loads(raw) if raw else {}
    except Exception:
        return {}
    if not isinstance(requested, dict):
        return {}
    return {spec["name"]: bool(requested.get(spec["name"], False)) for spec in get_outputs()}


def read_manual_overrides() -> Dict[str, bool]:
    """
    Manual overrides published by the web API, keyed by output name.

    The keys carry a Redis TTL, so an override the operator forgets about lapses
    back to automatic control on its own.
    """
    overrides: Dict[str, bool] = {}
    for spec in get_outputs():
        name = spec["name"]
        try:
            raw = redis_client.get(manual_override_key(name))
        except Exception:
            continue
        value = parse_bool_flag(raw)
        if value is not None:
            overrides[name] = value
    return overrides
