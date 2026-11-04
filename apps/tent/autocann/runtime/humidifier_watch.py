"""
Both watches over the humidifier, fed as one.

The relay says what the loop wants; it never says what the equipment did. Two
rules cover the two ways those come apart, and they are mirror images:

- `stuck_relay.py`: the output off and the tent climbing anyway — the contact
  stayed closed and the humidifier is still misting. Press POWER to stop it.
- `lost_pulse.py`: the output on, the tent below its band and the needle flat
  — the boot pulse was lost and the humidifier never left standby. Press
  POWER to start it.

Neither knows about the other, and nothing coordinates them: they cannot both
fire, because one needs the tent above its band and the other below it. What
they share is the four numbers they decide from, which is what lets the loop
hold one thing instead of a growing column of near-identical calls — the
reason this exists at all, since `cli/vpd.py` is the module that pays for
every one of them.
"""

from __future__ import annotations

from typing import Iterable, Protocol, Tuple

from autocann.runtime.lost_pulse import humidifier_restart_guard
from autocann.runtime.stuck_relay import humidifier_rescue_guard


class Guard(Protocol):
    """What a humidifier guard has to answer to. Returns True when it acted."""

    def observe(self, *, humidity: float, band_max: float,
                humidifier_on: bool, now: float) -> bool: ...


class HumidifierWatch:
    def __init__(self, guards: Iterable[Guard]) -> None:
        self._guards: Tuple[Guard, ...] = tuple(guards)

    def observe(self, *, humidity: float, band_max: float,
                humidifier_on: bool, now: float) -> None:
        """Feed every guard. Each decides on its own and reports for itself."""
        for guard in self._guards:
            guard.observe(humidity=humidity, band_max=band_max,
                          humidifier_on=humidifier_on, now=now)


def humidifier_watch() -> HumidifierWatch:
    """The pair the control loop runs."""
    return HumidifierWatch((humidifier_rescue_guard(), humidifier_restart_guard()))
