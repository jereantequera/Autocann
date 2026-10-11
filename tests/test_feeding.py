from __future__ import annotations

import pytest

from autocann.control.feeding import (
    chart_columns,
    effective_week,
    plan_for_week,
    scale_to_volume,
    week_of_stage,
    weeks_in_stage,
)


def product(pid, name, doses, unit="ml/L"):
    return {"id": pid, "name": name, "unit": unit, "doses": doses}


# A miniature chart: base every week of flowering except the last (the flush),
# a booster only in the middle, and something measured in grams.
PLAN = [
    product(1, "Base", {"early_veg": {1: 1, 2: 2, 3: 3},
                        "flowering": {1: 4, 2: 4, 3: 4, 4: 4, 5: 4, 6: 4, 7: 4}}),
    product(2, "Booster", {"flowering": {2: 2, 3: 2, 4: 2}}),
    product(3, "Micorrizas", {"early_veg": {1: 1}}, unit="g/L"),
]


# --------------------------------------------------------------------------
# weeks_in_stage
# --------------------------------------------------------------------------


def test_stage_length_sets_the_number_of_week_columns():
    # From STAGE_EXPECTED_DAYS: 21, 28, 56 and 10 days.
    assert weeks_in_stage("early_veg") == 3
    assert weeks_in_stage("late_veg") == 4
    assert weeks_in_stage("flowering") == 8
    assert weeks_in_stage("dry") == 2


def test_a_part_week_still_gets_its_own_column():
    """10 days is a week and a bit; the leftover days need somewhere to read from."""
    assert weeks_in_stage("x", {"x": 10}) == 2
    assert weeks_in_stage("x", {"x": 15}) == 3


def test_an_unknown_stage_gets_a_single_column():
    assert weeks_in_stage("no-such-stage") == 1
    assert weeks_in_stage("x", {"x": 0}) == 1


# --------------------------------------------------------------------------
# week_of_stage
# --------------------------------------------------------------------------


@pytest.mark.parametrize("day,week", [(1, 1), (7, 1), (8, 2), (14, 2), (15, 3), (56, 8)])
def test_seven_day_blocks_from_day_one(day, week):
    assert week_of_stage(day) == week


def test_a_stage_that_has_not_started_is_week_zero():
    """`stages.day_number` returns 0 before the start date; nothing is due then."""
    assert week_of_stage(0) == 0
    assert week_of_stage(-3) == 0
    assert week_of_stage(None) == 0


# --------------------------------------------------------------------------
# effective_week
# --------------------------------------------------------------------------


def test_a_normal_day_uses_its_own_week():
    assert effective_week("flowering", 16) == 3


def test_a_stage_running_long_keeps_the_last_week():
    """Day 70 of flowering is week 10; the chart only has 8, so it holds at 8."""
    assert effective_week("flowering", 70) == 8
    assert effective_week("early_veg", 40) == 3


def test_overrunning_a_flush_week_does_not_resume_feeding():
    """
    The regression this clamp exists for: flowering week 8 has no doses, so
    clamping to "the last week with doses" would send a grow on day 70 back to
    week 7 and start feeding again two weeks into the flush.
    """
    assert plan_for_week(PLAN, "flowering", effective_week("flowering", 49)) != []
    for day in (50, 56, 63, 70, 90):
        assert effective_week("flowering", day) == 8
        assert plan_for_week(PLAN, "flowering", effective_week("flowering", day)) == []


def test_a_stage_that_has_not_started_stays_at_zero():
    assert effective_week("flowering", 0) == 0


# --------------------------------------------------------------------------
# plan_for_week
# --------------------------------------------------------------------------


def test_only_the_products_due_that_week_come_back():
    due = plan_for_week(PLAN, "flowering", 3)
    assert [item["name"] for item in due] == ["Base", "Booster"]
    assert [item["dose"] for item in due] == [4, 2]


def test_a_week_with_nothing_due_is_a_flush():
    assert plan_for_week(PLAN, "flowering", 8) == []


def test_a_stage_absent_from_the_plan_is_empty():
    assert plan_for_week(PLAN, "dry", 1) == []


def test_week_zero_is_empty():
    assert plan_for_week(PLAN, "flowering", 0) == []


def test_the_unit_travels_with_the_product():
    due = plan_for_week(PLAN, "early_veg", 1)
    units = {item["name"]: item["unit"] for item in due}
    assert units == {"Base": "ml/L", "Micorrizas": "g/L"}


def test_weeks_keyed_as_strings_are_read_too():
    """A plan round-tripped through JSON has string keys; it must still match."""
    json_shaped = [product(1, "Base", {"flowering": {"2": 4}})]
    assert plan_for_week(json_shaped, "flowering", 2)[0]["dose"] == 4


def test_a_zero_or_unreadable_dose_is_skipped():
    noisy = [
        product(1, "Cero", {"flowering": {1: 0}}),
        product(2, "Vacío", {"flowering": {1: None}}),
        product(3, "Texto", {"flowering": {1: "mucho"}}),
        product(4, "Bien", {"flowering": {1: 2}}),
    ]
    assert [item["name"] for item in plan_for_week(noisy, "flowering", 1)] == ["Bien"]


# --------------------------------------------------------------------------
# scale_to_volume
# --------------------------------------------------------------------------


def test_the_dose_is_multiplied_by_the_litres():
    scaled = scale_to_volume(plan_for_week(PLAN, "flowering", 3), 12)
    assert [item["total"] for item in scaled] == [48, 24]


def test_totals_are_rounded_to_a_tenth():
    assert scale_to_volume([{"dose": 1.5}], 8.5)[0]["total"] == 12.8


def test_a_missing_volume_scales_to_nothing_rather_than_failing():
    assert scale_to_volume([{"dose": 4}], 0)[0]["total"] == 0
    assert scale_to_volume([{"dose": 4}], None)[0]["total"] == 0


def test_scaling_keeps_the_rest_of_the_item():
    scaled = scale_to_volume([{"name": "Base", "unit": "ml/L", "dose": 4}], 10)
    assert scaled[0]["name"] == "Base"
    assert scaled[0]["unit"] == "ml/L"


# --------------------------------------------------------------------------
# chart_columns
# --------------------------------------------------------------------------


def test_the_grid_has_one_column_per_week_of_every_stage():
    columns = chart_columns()
    assert len(columns) == 3 + 4 + 8 + 2
    assert columns[0] == {"stage": "early_veg", "week": 1}
    assert columns[-1] == {"stage": "dry", "week": 2}
