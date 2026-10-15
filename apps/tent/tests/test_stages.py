from __future__ import annotations

from datetime import datetime

import pytest

from autocann.control.stages import (
    build_timeline,
    current_period,
    day_number,
    estimated_remaining_days,
    timeline_to_dicts,
)
from autocann.control.vpd_math import STAGE_EXPECTED_DAYS
from autocann.time import ARGENTINA_TZ


def ts(year, month, day, hour=12, minute=0) -> int:
    """Epoch seconds for a local wall-clock time."""
    return int(ARGENTINA_TZ.localize(datetime(year, month, day, hour, minute)).timestamp())


# --------------------------------------------------------------------------
# day_number
# --------------------------------------------------------------------------


def test_the_start_date_is_day_one():
    start = ts(2026, 3, 10, 8, 0)
    assert day_number(start, start) == 1
    assert day_number(start, ts(2026, 3, 10, 23, 59)) == 1


def test_each_calendar_date_is_the_next_day():
    start = ts(2026, 3, 10)
    assert day_number(start, ts(2026, 3, 11)) == 2
    assert day_number(start, ts(2026, 3, 20)) == 11
    assert day_number(start, ts(2026, 4, 10)) == 32


def test_a_late_night_stage_change_does_not_reset_the_counter():
    """
    Switching at 23:59 and checking a minute later must read day 2, not day 1
    again — the counter follows the calendar, not elapsed hours.
    """
    start = ts(2026, 3, 10, 23, 59)
    assert day_number(start, start) == 1
    assert day_number(start, ts(2026, 3, 11, 0, 1)) == 2
    assert day_number(start, ts(2026, 3, 11, 23, 59)) == 2


def test_two_hours_apart_across_midnight_still_counts_as_two_days():
    """Elapsed-time arithmetic would answer 1 here; the calendar answers 2."""
    assert day_number(ts(2026, 3, 10, 23, 0), ts(2026, 3, 11, 1, 0)) == 2


def test_a_whole_day_inside_one_date_stays_on_day_one():
    """And the mirror case: 23 hours within one date is still day 1."""
    assert day_number(ts(2026, 3, 10, 0, 30), ts(2026, 3, 10, 23, 30)) == 1


def test_counting_across_a_month_and_a_year_boundary():
    assert day_number(ts(2026, 1, 30), ts(2026, 2, 2)) == 4
    assert day_number(ts(2026, 12, 30), ts(2027, 1, 2)) == 4
    assert day_number(ts(2024, 2, 27), ts(2024, 3, 1)) == 4   # 2024 es bisiesto


def test_a_start_in_the_future_reads_as_not_started():
    assert day_number(ts(2026, 3, 20), ts(2026, 3, 10)) == 0


# --------------------------------------------------------------------------
# build_timeline
# --------------------------------------------------------------------------


def test_an_empty_history_yields_an_empty_timeline():
    assert build_timeline([], now=ts(2026, 3, 10)) == []


def test_a_single_stage_is_open_and_measured_against_now():
    events = [{"stage": "early_veg", "started_at": ts(2026, 3, 1)}]
    timeline = build_timeline(events, now=ts(2026, 3, 10))
    assert len(timeline) == 1
    period = timeline[0]
    assert (period.stage, period.days, period.is_current) == ("early_veg", 10, True)
    assert period.ended_at is None
    assert period.expected_days == STAGE_EXPECTED_DAYS["early_veg"]


def test_closed_periods_end_where_the_next_one_starts():
    events = [
        {"stage": "early_veg", "started_at": ts(2026, 3, 1)},
        {"stage": "late_veg", "started_at": ts(2026, 3, 22)},
        {"stage": "flowering", "started_at": ts(2026, 4, 19)},
    ]
    timeline = build_timeline(events, now=ts(2026, 5, 1))
    assert [p.stage for p in timeline] == ["early_veg", "late_veg", "flowering"]
    assert [p.days for p in timeline] == [21, 28, 13]
    assert [p.is_current for p in timeline] == [False, False, True]
    assert timeline[0].ended_at == ts(2026, 3, 22)


def test_events_out_of_order_are_sorted_before_being_paired():
    events = [
        {"stage": "flowering", "started_at": ts(2026, 4, 19)},
        {"stage": "early_veg", "started_at": ts(2026, 3, 1)},
        {"stage": "late_veg", "started_at": ts(2026, 3, 22)},
    ]
    timeline = build_timeline(events, now=ts(2026, 5, 1))
    assert [p.stage for p in timeline] == ["early_veg", "late_veg", "flowering"]


def test_two_changes_on_the_same_date_still_count_as_one_day():
    """A stage corrected minutes after being set must not report zero days."""
    events = [
        {"stage": "early_veg", "started_at": ts(2026, 3, 1, 9, 0)},
        {"stage": "late_veg", "started_at": ts(2026, 3, 1, 9, 30)},
    ]
    timeline = build_timeline(events, now=ts(2026, 3, 5))
    assert timeline[0].days == 1
    assert timeline[1].days == 5


def test_progress_is_capped_and_absent_without_an_expected_duration():
    events = [{"stage": "flowering", "started_at": ts(2026, 1, 1)}]
    period = build_timeline(events, now=ts(2026, 12, 1))[0]
    assert period.progress == 1.0            # long overdue, still capped

    custom = build_timeline(events, now=ts(2026, 1, 1), expected={})[0]
    assert custom.progress is None


def test_current_period_is_the_open_one():
    events = [
        {"stage": "early_veg", "started_at": ts(2026, 3, 1)},
        {"stage": "flowering", "started_at": ts(2026, 4, 1)},
    ]
    timeline = build_timeline(events, now=ts(2026, 4, 10))
    assert current_period(timeline).stage == "flowering"
    assert current_period([]) is None


# --------------------------------------------------------------------------
# Harvest estimate
# --------------------------------------------------------------------------


def test_remaining_days_add_up_the_rest_of_the_cycle():
    events = [{"stage": "late_veg", "started_at": ts(2026, 3, 1)}]
    remaining = estimated_remaining_days(build_timeline(events, now=ts(2026, 3, 15)))
    # day 15 of a 28-day late_veg -> 13 left, plus flowering (56) and dry (10)
    assert remaining == 13 + 56 + 10


def test_an_overdue_stage_does_not_make_the_estimate_go_backwards():
    events = [{"stage": "flowering", "started_at": ts(2026, 1, 1)}]
    remaining = estimated_remaining_days(build_timeline(events, now=ts(2026, 6, 1)))
    assert remaining == STAGE_EXPECTED_DAYS["dry"]    # flowering contributes 0, never negative


def test_the_last_stage_leaves_only_its_own_remainder():
    events = [{"stage": "dry", "started_at": ts(2026, 3, 1)}]
    assert estimated_remaining_days(build_timeline(events, now=ts(2026, 3, 5))) == 10 - 5


def test_no_estimate_without_a_timeline_or_a_known_stage():
    assert estimated_remaining_days([]) is None
    events = [{"stage": "raro", "started_at": ts(2026, 3, 1)}]
    assert estimated_remaining_days(build_timeline(events, now=ts(2026, 3, 5))) is None


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------


def test_the_api_view_carries_dates_and_flags():
    events = [
        {"stage": "early_veg", "started_at": ts(2026, 3, 1)},
        {"stage": "flowering", "started_at": ts(2026, 3, 22)},
    ]
    rows = timeline_to_dicts(build_timeline(events, now=ts(2026, 4, 1)))
    assert rows[0]["started_date"] == "2026-03-01"
    assert rows[0]["ended_date"] == "2026-03-22"
    assert rows[1]["ended_date"] is None
    assert rows[1]["is_current"] is True
    assert 0 < rows[1]["progress"] < 1


@pytest.mark.parametrize("hour", range(0, 24, 3))
def test_the_day_number_is_the_same_whatever_time_of_day_is_checked(hour):
    start = ts(2026, 3, 10, 7, 0)
    assert day_number(start, ts(2026, 3, 15, hour, 0)) == 6
