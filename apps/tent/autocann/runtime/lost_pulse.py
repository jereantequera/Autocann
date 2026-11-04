"""
A humidifier that has power but never started, and how the loop notices.

The relay only enables the humidifier: the unit comes up in standby and the
relay node presses its POWER button over IR three seconds later
(firmware/relay_node/relay_node.ino). When that pulse is lost the cycle passes
without mist, which the firmware calls the safe side of the failure — and it
is, for the tent. What it is not is self-healing, and that is this module.

A lost pulse locks itself in. No mist means the humidity does not rise, which
means the loop never reaches its band, which means it never turns the output
off, which means there is no new off→on transition to hang another pulse on.
On 2026-11-02 that cost six hours: humidity_up energised from 14:15 to the
evening with the tent tracking the room to within a fifth of a point, while
the panel, the history and the loop all reported a humidifier hard at work.

The trigger is the output on, the tent still below its band, well past the
boot pulse, and the needle flat. It is specific because a working humidifier
here is loud: eight points in the first minute, so the loop is back out of
HUMIDIFY within a couple of them. An output that has been on for five minutes
with nothing to show has already ruled out the normal case, and a rise of one
point over five minutes is still more than ten times the drift this tent gets
from the room around it. The band is the mirror of `stuck_relay.py`, which
fires above it: between the two, nothing fires for a tent that is where it
was asked to be.

The press goes out as an ordinary dashboard press rather than a rescue: the
output is on, so the equipment has power, which is exactly the state in which
the node sends one. The relay is not cycled. Cutting it would also work — it
is what HARDWARE.md tells a person to do by hand — but it throws away a known
state and a minimum-off time to arrive at the same pulse.

POWER is a toggle, so the one wrong case is a humidifier that was running
while the humidity stayed flat for some other reason: the press would stop it.
That is why the window is long and the bar is a rise of almost nothing, and
why a second press is allowed — flat is still flat afterwards, so the second
one puts the equipment back where it was. After MAX_PRESSES the guard stops
pressing and only says so.
"""

from __future__ import annotations

from collections import deque
from typing import Callable, Deque, Optional, Tuple

#: How long the output has to have been on before a flat needle counts. Well
#: past the node's boot pulse, and past the couple of minutes a working
#: humidifier needs to push this tent out of its band and the output back off.
ON_GRACE_SECONDS = 300.0

#: The window the rise is measured over, and the rise that still counts as
#: nothing happening. The humidifier does around eight points in a minute; the
#: room drifts this tent about a tenth of one over the same five minutes.
FLAT_WINDOW_SECONDS = 300.0
FLAT_PERCENT = 1.0

#: Between presses in one on period, and how many at most.
RETRY_AFTER_SECONDS = 300.0
MAX_PRESSES = 2

#: Forget everything after a gap this long without being fed. The loop skips
#: this guard in test mode, in monitor mode and while the reading is stale, and
#: an "on since" from before such a gap times a tent nobody was driving.
STALE_OBSERVATION_SECONDS = 60.0

#: The control_events row a press leaves, which the logbook spells out.
LOST_PULSE_EVENT = "humidity_up_standby"


class LostPulseGuard:
    """
    Fed every loop iteration; calls `send_press` when the mist never came.

    `send_press` is injected so the rule can be tested without Redis, and so
    a failure to queue the press is the caller's to report.
    """

    def __init__(self, send_press: Callable[[], bool], log: Callable[[str], None] = print) -> None:
        self._send_press = send_press
        self._log = log
        self._readings: Deque[Tuple[float, float]] = deque()
        self._on_since: Optional[float] = None
        self._presses = 0
        self._last_press: Optional[float] = None
        self._gave_up = False
        self._last_seen: Optional[float] = None

    def reset(self) -> None:
        """Forget this on period. The next one starts its own clock."""
        self._readings.clear()
        self._on_since = None
        self._presses = 0
        self._last_press = None
        self._gave_up = False

    def observe(self, *, humidity: float, band_max: float,
                humidifier_on: bool, now: float) -> bool:
        """Returns True when a press was sent on this call."""
        if self._last_seen is not None and now - self._last_seen > STALE_OBSERVATION_SECONDS:
            self.reset()
        self._last_seen = now

        if not humidifier_on:
            # A new off→on transition gets its own boot pulse, so whatever this
            # guard was building up about the last one no longer describes
            # anything.
            self.reset()
            return False
        if self._on_since is None:
            self._on_since = now

        self._readings.append((now, humidity))
        while self._readings and now - self._readings[0][0] > FLAT_WINDOW_SECONDS:
            self._readings.popleft()

        if now - self._on_since < ON_GRACE_SECONDS:
            return False
        # Only while the tent is still short of the band's top. Above it the
        # loop has stopped asking for mist, so an output that is on anyway is
        # somebody's override — and a tent sitting saturated under one is flat
        # for the most ordinary reason there is.
        if humidity >= band_max:
            return False
        # The window has to be mostly there, or two readings a second apart
        # would make any tent look flat.
        if now - self._readings[0][0] < FLAT_WINDOW_SECONDS * 0.75:
            return False
        rise = humidity - min(h for _, h in self._readings)
        if rise >= FLAT_PERCENT:
            return False
        if self._last_press is not None and now - self._last_press < RETRY_AFTER_SECONDS:
            return False

        if self._presses >= MAX_PRESSES:
            if not self._gave_up:
                self._gave_up = True
                self._log(f"💨 Humidifier still not misting after {MAX_PRESSES} IR presses "
                          f"({humidity:.1f}%, +{rise:.1f} pts in "
                          f"{FLAT_WINDOW_SECONDS / 60:.0f} min): not pressing more. "
                          "Check the water and the IR LED.")
            return False

        self._last_press = now
        if not self._send_press():
            self._log("💨 Humidifier looks like it never started, but the IR press "
                      "could not be queued")
            return False
        self._presses += 1
        minutes = (now - self._on_since) / 60
        self._log(f"💨 Humidifier on for {minutes:.0f} min and humidity only +{rise:.1f} pts in "
                  f"{FLAT_WINDOW_SECONDS / 60:.0f} min ({humidity:.1f}%): the boot pulse was "
                  f"lost, IR ON sent ({self._presses}/{MAX_PRESSES})")
        return True


def humidifier_restart_guard() -> LostPulseGuard:
    """The guard the control loop runs, queueing its press on the relay node's poll."""
    from autocann.hardware.ir_remote import enqueue, find_button
    from autocann.runtime import redis as runtime_redis
    from autocann.store.samples import store_control_event

    def send() -> bool:
        try:
            # None means a press somebody else queued is still in flight. Two
            # pulses undo each other, so this one is dropped rather than
            # stacked; the retry delay has already started.
            if enqueue(runtime_redis.redis_client, find_button("power")) is None:
                return False
        except Exception:
            return False
        store_control_event(LOST_PULSE_EVENT, "ir_on")
        return True

    return LostPulseGuard(send_press=send)
