"""
A patch for the humidifier's relay sticking closed, until the wiring changes.

On 2026-10-29 `humidity_up` went off at 17:23 and at 20:45 and the tent went
on to 97 % and 94 %: the relay had stayed closed, so the humidifier kept its
power and kept misting. The loop cannot open a stuck contact, but the relay
node can press the humidifier's POWER button over IR.

The trigger is the output off and humidity climbing fast above the band. That
is safe to act on because of what the pulse does in each case:

- Relay really open: the humidifier has no power and the pulse does nothing.
- Relay stuck, humidifier running (the case above): the pulse turns it off.
- Relay stuck, humidifier already off, humidity rising for another reason:
  the pulse turns it ON. That is the one wrong case, and why the rise has to
  be fast — the humidifier did 20 points in four minutes, the tent alone does
  not — and why a second rescue is allowed: if the first one turned it on,
  humidity keeps rising and the second turns it back off.

After MAX_RESCUES the guard stops pressing and only says so: by then the
pulses are clearly not what is going on.
"""

from __future__ import annotations

from collections import deque
from typing import Callable, Deque, Optional, Tuple

#: How long the output has to be off before a rise counts. Humidity carries on
#: up for a moment after a working humidifier stops.
OFF_GRACE_SECONDS = 120.0

#: The rise that only a running humidifier explains, and over how long.
RISE_WINDOW_SECONDS = 180.0
RISE_PERCENT = 5.0

#: The control_events row a rescue leaves, which the logbook spells out.
STUCK_RESCUE_EVENT = "humidity_up_stuck"

#: Between rescues in one off period, and how many at most.
RETRY_AFTER_SECONDS = 300.0
MAX_RESCUES = 2


class StuckRelayGuard:
    """
    Fed every loop iteration; calls `send_rescue` when the relay looks stuck.

    `send_rescue` is injected so the rule can be tested without Redis, and so
    a failure to queue the pulse is the caller's to report.
    """

    def __init__(self, send_rescue: Callable[[], bool], log: Callable[[str], None] = print) -> None:
        self._send_rescue = send_rescue
        self._log = log
        self._readings: Deque[Tuple[float, float]] = deque()
        self._off_since: Optional[float] = None
        self._rescues = 0
        self._last_rescue: Optional[float] = None
        self._gave_up = False

    def observe(self, *, humidity: float, band_max: float, humidifier_on: bool, now: float) -> bool:
        """Returns True when a rescue was sent on this call."""
        self._readings.append((now, humidity))
        while self._readings and now - self._readings[0][0] > RISE_WINDOW_SECONDS:
            self._readings.popleft()

        if humidifier_on:
            self._off_since, self._rescues, self._last_rescue, self._gave_up = None, 0, None, False
            return False
        if self._off_since is None:
            self._off_since = now

        if now - self._off_since < OFF_GRACE_SECONDS or humidity <= band_max:
            return False
        # The window has to be mostly there, or a single early reading would
        # make any rise look instant.
        if now - self._readings[0][0] < RISE_WINDOW_SECONDS * 0.75:
            return False
        rise = humidity - min(h for _, h in self._readings)
        if rise < RISE_PERCENT:
            return False
        if self._last_rescue is not None and now - self._last_rescue < RETRY_AFTER_SECONDS:
            return False

        if self._rescues >= MAX_RESCUES:
            if not self._gave_up:
                self._gave_up = True
                self._log(f"🛟 Humidity still rising with the humidifier off ({humidity:.1f}%) after "
                          f"{MAX_RESCUES} IR rescues: not sending more. Check the relay.")
            return False

        self._last_rescue = now
        if not self._send_rescue():
            self._log("🛟 Humidifier relay looks stuck, but the IR rescue could not be queued")
            return False
        self._rescues += 1
        self._log(f"🛟 Humidifier off but humidity +{rise:.1f} pts in {RISE_WINDOW_SECONDS / 60:.0f} min "
                  f"({humidity:.1f}% > {band_max:.0f}%): relay looks stuck, IR OFF sent "
                  f"({self._rescues}/{MAX_RESCUES})")
        return True


def humidifier_rescue_guard() -> StuckRelayGuard:
    """The guard the control loop runs, queueing its rescue on the relay node's poll."""
    from autocann.hardware.ir_remote import enqueue_rescue
    from autocann.runtime import redis as runtime_redis
    from autocann.store.samples import store_control_event

    def send() -> bool:
        try:
            enqueue_rescue(runtime_redis.redis_client)
        except Exception:
            return False
        store_control_event(STUCK_RESCUE_EVENT, "ir_off")
        return True

    return StuckRelayGuard(send_rescue=send)
