"""
The day/night classifier: pure, so these need no database.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from autocann.control.daynight import is_lights_on, local_hour_of, split_rows
from autocann.time import ARGENTINA_TZ


def hours_on(on, off):
    """Every whole hour of the day the lights are on, for a window."""
    return [hour for hour in range(24) if is_lights_on(hour, on, off)]


def test_a_window_inside_the_day_is_on_between_its_edges():
    assert hours_on(6, 18) == list(range(6, 18))


def test_the_on_edge_is_on_and_the_off_edge_is_off():
    assert is_lights_on(6.0, 6, 18) is True
    assert is_lights_on(17.99, 6, 18) is True
    assert is_lights_on(18.0, 6, 18) is False
    assert is_lights_on(5.99, 6, 18) is False


def test_an_overnight_window_crosses_midnight():
    # On at 18:00, off at 12:00 the next day: 18 hours.
    assert hours_on(18, 12) == list(range(0, 12)) + list(range(18, 24))


def test_the_overnight_edges_sit_where_the_window_says():
    assert is_lights_on(18.0, 18, 12) is True
    assert is_lights_on(17.99, 18, 12) is False
    assert is_lights_on(11.99, 18, 12) is True
    assert is_lights_on(12.0, 18, 12) is False


def test_midnight_is_a_valid_off_hour():
    # The default: on at 06:00, off at 00:00, 18 hours of light.
    assert hours_on(6, 0) == list(range(6, 24))
    assert is_lights_on(0.0, 6, 0) is False
    assert is_lights_on(23.99, 6, 0) is True


def test_a_window_of_one_hour_is_one_hour():
    assert hours_on(9, 10) == [9]


def test_equal_hours_are_refused_rather_than_read_as_always_off():
    with pytest.raises(ValueError):
        is_lights_on(3.0, 6, 6)


def test_a_fraction_of_an_hour_counts_on_its_own_side_of_the_edge():
    assert is_lights_on(6.5, 6, 7) is True
    assert is_lights_on(7.5, 6, 7) is False


def test_local_hour_of_reads_the_clock_in_the_given_zone():
    ts = int(ARGENTINA_TZ.localize(datetime(2026, 10, 1, 6, 30)).timestamp())
    assert local_hour_of(ts, ARGENTINA_TZ) == pytest.approx(6.5)


def test_split_rows_keeps_order_and_partitions_every_row():
    base = int(ARGENTINA_TZ.localize(datetime(2026, 10, 1)).timestamp())
    rows = [{"timestamp": base + h * 3600, "hour": h} for h in range(24)]

    day, night = split_rows(rows, 6, 0, ARGENTINA_TZ)

    assert [r["hour"] for r in day] == list(range(6, 24))
    assert [r["hour"] for r in night] == list(range(0, 6))
    assert len(day) + len(night) == len(rows)


def test_split_rows_handles_an_overnight_window():
    base = int(ARGENTINA_TZ.localize(datetime(2026, 10, 1)).timestamp())
    rows = [{"timestamp": base + h * 3600} for h in range(24)]

    day, night = split_rows(rows, 18, 12, ARGENTINA_TZ)

    assert len(day) == 18
    assert len(night) == 6


def test_split_rows_of_nothing_is_two_empty_lists():
    assert split_rows([], 6, 0, ARGENTINA_TZ) == ([], [])
