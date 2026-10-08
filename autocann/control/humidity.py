"""
Humidity control decisions, as pure functions.

The old control loop decided on the raw reading every 3 seconds with no deadband
and no minimum run time, so a noisy sensor (a DHT22 jitters several % RH between
reads) could switch a humidifier or a dehumidifier compressor on and off every
few seconds. That is hard on the hardware and keeps the tent oscillating.

This module keeps the decision separate from the GPIO and the sensors so it can
be unit tested, and adds three protections:

- a **deadband** around the setpoint, so noise alone never starts a device;
- a **minimum on time** and a **minimum off time** per device, so nothing can
  short-cycle regardless of what the sensor reports;
- a **failsafe**: a missing or stale reading turns everything off immediately,
  bypassing the minimum on time.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from statistics import median
from typing import Deque, Dict, Optional

IDLE = "idle"
HUMIDIFY = "humidify"
DEHUMIDIFY = "dehumidify"

#: Reasons that mean "we do not know the state of the tent". A decision carrying
#: one of these must de-energise everything, including manually overridden
#: outputs: an override is a human saying "I know what I am doing", which stops
#: being true once the sensors go dark.
FAILSAFE_REASONS = frozenset({"no_data", "stale_data"})


@dataclass(frozen=True)
class ControllerConfig:
    #: Do not *start* a device while the reading is within ±deadband of target.
    deadband_pct: float = 2.0
    #: Once a device is on, leave it on at least this long.
    min_on_seconds: float = 60.0
    #: Once a device is off, leave *that* device off at least this long
    #: (compressor protection).
    min_off_seconds: float = 180.0
    #: After any device stops, leave everything off at least this long before
    #: starting anything. Without it the humidifier could stop and the
    #: dehumidifier start seconds later, so the two spend the day undoing each
    #: other's work.
    min_changeover_seconds: float = 120.0
    #: A reading older than this is treated as no reading at all.
    stale_after_seconds: float = 120.0


#: Used when a caller does not supply one. Frozen, so sharing it is safe.
DEFAULT_CONFIG = ControllerConfig()


@dataclass
class ControllerState:
    action: str = IDLE
    changed_at: float = 0.0
    #: When each device was last switched off, keyed by action.
    last_off_at: Dict[str, float] = field(default_factory=dict)
    #: When *any* device was last switched off.
    last_stop_at: Optional[float] = None


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    changed: bool

    @property
    def failsafe(self) -> bool:
        """True when this decision was forced by missing or stale data."""
        return self.reason in FAILSAFE_REASONS

    @property
    def humidify(self) -> bool:
        return self.action == HUMIDIFY

    @property
    def dehumidify(self) -> bool:
        return self.action == DEHUMIDIFY


def decide(
    *,
    humidity: Optional[float],
    target_humidity: Optional[float],
    now: float,
    state: ControllerState,
    config: Optional[ControllerConfig] = None,
    reading_age_seconds: float = 0.0,
) -> Decision:
    """
    Decide what the humidity outputs should do, and mutate `state` to match.

    `now` is a monotonic clock reading (seconds). `humidity` and
    `target_humidity` are percentages; either being None means "no valid data"
    and trips the failsafe.
    """
    config = config or DEFAULT_CONFIG
    # --- Failsafe: no usable data. Stop everything, ignore minimum on time. ---
    if humidity is None or target_humidity is None:
        return _transition(state, IDLE, "no_data", now)
    if reading_age_seconds > config.stale_after_seconds:
        return _transition(state, IDLE, "stale_data", now)

    error = humidity - target_humidity
    elapsed = now - state.changed_at

    # --- A device is already running: decide whether to stop. ---
    if state.action in (HUMIDIFY, DEHUMIDIFY):
        reached = error >= 0 if state.action == HUMIDIFY else error <= 0
        if not reached:
            return Decision(state.action, "still_correcting", changed=False)
        if elapsed < config.min_on_seconds:
            # Target reached, but stopping now would short-cycle. Overshooting a
            # little is cheaper than hammering the relay.
            return Decision(state.action, "min_on_time", changed=False)
        return _transition(state, IDLE, "target_reached", now)

    # --- Idle: decide whether to start, and in which direction. ---
    if abs(error) <= config.deadband_pct:
        return Decision(IDLE, "in_deadband", changed=False)

    if (
        state.last_stop_at is not None
        and (now - state.last_stop_at) < config.min_changeover_seconds
    ):
        return Decision(IDLE, "changeover_delay", changed=False)

    wanted = HUMIDIFY if error < 0 else DEHUMIDIFY
    last_off = state.last_off_at.get(wanted)
    if last_off is not None and (now - last_off) < config.min_off_seconds:
        return Decision(IDLE, "anti_short_cycle", changed=False)

    return _transition(state, wanted, "correcting", now)


def _transition(state: ControllerState, action: str, reason: str, now: float) -> Decision:
    if state.action == action:
        return Decision(action, reason, changed=False)

    if state.action in (HUMIDIFY, DEHUMIDIFY):
        state.last_off_at[state.action] = now
        state.last_stop_at = now

    state.action = action
    state.changed_at = now
    return Decision(action, reason, changed=True)


class MedianFilter:
    """
    Rolling median over the last `size` samples.

    A median rejects the isolated spikes cheap sensors produce without the lag a
    moving average adds. `size` should stay small and odd.
    """

    def __init__(self, size: int = 5) -> None:
        if size < 1:
            raise ValueError("size must be >= 1")
        self.size = size
        self._values: Deque[float] = deque(maxlen=size)

    def push(self, value: Optional[float]) -> Optional[float]:
        """Add a sample (None is ignored) and return the current median."""
        if value is not None:
            self._values.append(float(value))
        return self.value

    @property
    def value(self) -> Optional[float]:
        if not self._values:
            return None
        return round(median(self._values), 2)

    @property
    def ready(self) -> bool:
        return len(self._values) >= self.size

    def clear(self) -> None:
        self._values.clear()
