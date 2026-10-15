"""
Quiet hours: the window in which the controller may not start a noisy output.
"""

from __future__ import annotations

from datetime import time

import pytest

from autocann.control.humidity import (
    DEHUMIDIFY,
    HUMIDIFY,
    IDLE,
    ControllerConfig,
    ControllerState,
    decide,
)
from autocann.control.quiet import QuietWindow, blocked_actions, parse_hhmm, windows_from_dict

NIGHT = QuietWindow(enabled=True, start=time(23, 0), end=time(7, 0))
DAY = QuietWindow(enabled=True, start=time(9, 0), end=time(17, 0))


# ---------------------------------------------------------------------------
# The window itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hhmm,inside", [
    ("23:00", True), ("23:30", True), ("03:00", True), ("06:59", True),
    ("07:00", False), ("12:00", False), ("22:59", False),
])
def test_a_window_that_crosses_midnight_is_one_night(hhmm, inside):
    """
    The only window anybody actually wants. Read as `start <= now < end` it
    would match nothing at all.
    """
    assert NIGHT.contains(parse_hhmm(hhmm)) is inside


@pytest.mark.parametrize("hhmm,inside", [
    ("09:00", True), ("12:00", True), ("16:59", True), ("17:00", False), ("08:59", False),
])
def test_a_window_inside_one_day_still_works(hhmm, inside):
    assert DAY.contains(parse_hhmm(hhmm)) is inside


def test_a_zero_width_window_silences_nothing():
    """Reading it as 'all day' would mute an output from an unfinished edit."""
    window = QuietWindow(enabled=True, start=time(3, 0), end=time(3, 0))
    assert window.contains(time(3, 0)) is False
    assert window.contains(time(14, 0)) is False


def test_a_window_with_no_bounds_is_not_applied():
    assert QuietWindow(enabled=True).contains(time(3, 0)) is False


def test_a_disabled_window_is_not_applied():
    assert QuietWindow(enabled=False, start=time(23, 0), end=time(7, 0)).contains(time(3, 0)) is False


def test_the_humidity_escape_reopens_the_window():
    """Silence is not worth a crop: a night at 85 % is mould."""
    window = QuietWindow(enabled=True, start=time(23, 0), end=time(7, 0), max_humidity=85.0)

    assert window.blocks(time(3, 0), humidity=70.0) is True
    assert window.blocks(time(3, 0), humidity=85.0) is False
    assert window.blocks(time(3, 0), humidity=92.0) is False


def test_no_escape_configured_is_a_hard_block():
    assert NIGHT.blocks(time(3, 0), humidity=99.0) is True


def test_a_missing_reading_cannot_open_the_escape():
    """Without a humidity there is no evidence of a problem worth the noise."""
    window = QuietWindow(enabled=True, start=time(23, 0), end=time(7, 0), max_humidity=85.0)
    assert window.blocks(time(3, 0), humidity=None) is True


# ---------------------------------------------------------------------------
# Storage round-trip
# ---------------------------------------------------------------------------


def test_a_window_survives_being_stored_and_read_back():
    stored = NIGHT.to_dict()
    assert QuietWindow.from_dict(stored) == NIGHT


@pytest.mark.parametrize("value", ["", "25:00", "07:60", "siete", "7", None, 700, "7:00:00"])
def test_a_time_that_is_not_a_time_reads_as_absent(value):
    """A corrupt row must not stop the loop: an unreadable window just does not apply."""
    assert parse_hhmm(value) is None


def test_corrupt_stored_configuration_yields_no_windows():
    assert windows_from_dict("no soy un dict") == {}
    assert windows_from_dict({"humidity_down": "tampoco"}) == {}


# ---------------------------------------------------------------------------
# What the controller does with it
# ---------------------------------------------------------------------------


def _blocked_at(hhmm, humidity=70.0):
    return blocked_actions({"humidity_down": NIGHT}, parse_hhmm(hhmm), humidity)


def test_a_window_blocks_the_action_its_output_belongs_to():
    assert _blocked_at("03:00") == frozenset({DEHUMIDIFY})
    assert _blocked_at("12:00") == frozenset()


def test_a_window_on_an_output_nothing_drives_blocks_nothing():
    """Ventilation has no automatic action today, so a window on it is inert."""
    assert blocked_actions({"ventilation": NIGHT}, time(3, 0), 70.0) == frozenset()


def test_the_controller_does_not_start_a_blocked_output():
    state = ControllerState()
    decision = decide(
        humidity=80.0, target_humidity=40.0, now=1000.0, state=state,
        config=ControllerConfig(), blocked=frozenset({DEHUMIDIFY}),
    )
    assert (decision.action, decision.reason) == (IDLE, "quiet_hours")


def test_the_other_direction_is_untouched_by_the_block():
    state = ControllerState()
    decision = decide(
        humidity=20.0, target_humidity=40.0, now=1000.0, state=state,
        config=ControllerConfig(), blocked=frozenset({DEHUMIDIFY}),
    )
    assert decision.action == HUMIDIFY


def test_a_window_opening_stops_a_running_output():
    config = ControllerConfig(min_on_seconds=60.0)
    state = ControllerState()
    decide(humidity=80.0, target_humidity=40.0, now=0.0, state=state, config=config)
    assert state.action == DEHUMIDIFY

    decision = decide(
        humidity=80.0, target_humidity=40.0, now=120.0, state=state, config=config,
        blocked=frozenset({DEHUMIDIFY}),
    )
    assert (decision.action, decision.reason) == (IDLE, "quiet_hours")


def test_a_window_never_cuts_a_minimum_on_time_short():
    """
    The block is about noise; the minimum is about the equipment, and the
    equipment wins.
    """
    config = ControllerConfig(min_on_seconds=60.0)
    state = ControllerState()
    decide(humidity=80.0, target_humidity=40.0, now=0.0, state=state, config=config)

    decision = decide(
        humidity=80.0, target_humidity=40.0, now=30.0, state=state, config=config,
        blocked=frozenset({DEHUMIDIFY}),
    )
    assert (decision.action, decision.reason) == (DEHUMIDIFY, "min_on_time")


def test_the_controllers_timers_stay_true_through_a_window():
    """
    The reason this constrains the decision instead of forcing the contact: a
    stop during the window is a real stop, so the anti-short-cycle delay applies
    afterwards instead of the loop believing the fan ran all night.
    """
    config = ControllerConfig(min_on_seconds=10.0, min_off_seconds=180.0,
                              min_changeover_seconds=0.0)
    state = ControllerState()
    decide(humidity=80.0, target_humidity=40.0, now=0.0, state=state, config=config)
    decide(humidity=80.0, target_humidity=40.0, now=100.0, state=state, config=config,
           blocked=frozenset({DEHUMIDIFY}))
    assert state.action == IDLE

    # Window over, still too humid - but the output only just stopped.
    decision = decide(humidity=80.0, target_humidity=40.0, now=150.0, state=state, config=config)
    assert (decision.action, decision.reason) == (IDLE, "anti_short_cycle")
