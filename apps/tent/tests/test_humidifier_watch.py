"""
The pair of humidifier guards the loop feeds (autocann/runtime/humidifier_watch.py).

The loop makes one call; what this has to hold is that the call reaches every
guard, and that the real pair is still both of them.
"""

from __future__ import annotations

from autocann.runtime.humidifier_watch import HumidifierWatch, humidifier_watch
from autocann.runtime.lost_pulse import LostPulseGuard
from autocann.runtime.stuck_relay import StuckRelayGuard


class Spy:
    def __init__(self):
        self.seen = []

    def observe(self, **reading):
        self.seen.append(reading)
        return False


READING = {"humidity": 65.5, "band_max": 79.0, "humidifier_on": True, "now": 1000.0}


def test_every_guard_is_fed_the_same_reading():
    spies = [Spy(), Spy(), Spy()]
    HumidifierWatch(spies).observe(**READING)
    assert all(spy.seen == [READING] for spy in spies)


def test_a_guard_that_acts_does_not_stop_the_others():
    """Each one decides on its own; the watch is a fan-out, not a chain."""
    acted = Spy()
    acted.observe = lambda **reading: True
    last = Spy()
    HumidifierWatch([acted, last]).observe(**READING)
    assert last.seen == [READING]


def test_the_real_watch_runs_both_halves():
    """
    Named explicitly: the two cover opposite failures, and a watch that
    quietly lost one would still look like it was working.
    """
    guards = humidifier_watch()._guards
    assert [type(g) for g in guards] == [StuckRelayGuard, LostPulseGuard]
