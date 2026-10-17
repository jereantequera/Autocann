from __future__ import annotations

from datetime import datetime

import pytest

from autocann.control.vpd_math import VPD_RANGES
from autocann.paths import DATA_DIR
from autocann.sim.history import default_gaps, lights_on, plan_history, synthetic_history
from autocann.sim.seed import _sandbox_store, check_seed_path, seed_history
from autocann.store.grow_backdate import backdate_grow
from autocann.store.grows import create_grow, get_all_grows, get_stage_events
from autocann.store.irrigations import get_irrigations
from autocann.store.samples import get_sensor_data_range
from autocann.store.schema import ensure_schema
from autocann.time import ARGENTINA_TZ

NOW = 1_800_000_000  # a fixed instant; the rows are a function of it


def test_row_count_is_days_times_288():
    assert len(synthetic_history(2, NOW)) == 2 * 288


def test_rows_are_300_seconds_apart():
    timestamps = [r["timestamp"] for r in synthetic_history(1, NOW)]
    assert {b - a for a, b in zip(timestamps, timestamps[1:])} == {300}


def test_gaps_drop_their_rows_and_leave_holes():
    rows = synthetic_history(2, NOW, gaps=[(10.0, 2.0)])
    assert len(rows) == 2 * 288 - 24
    timestamps = [r["timestamp"] for r in rows]
    spacing = [b - a for a, b in zip(timestamps, timestamps[1:])]
    assert spacing.count(300) == len(spacing) - 1
    assert max(spacing) == 2 * 3600 + 300  # the hole is exactly the two hours dropped


def test_plan_with_default_gaps_has_two_holes():
    plan = plan_history(7, NOW, gaps=default_gaps(7))
    assert len(plan.rows) == 7 * 288 - 2 * 24


def test_days_are_warmer_than_nights():
    rows = synthetic_history(3, NOW)
    day = [r["temperature"] for r in rows if lights_on(r["timestamp"], 18.0)]
    night = [r["temperature"] for r in rows if not lights_on(r["timestamp"], 18.0)]
    assert sum(day) / len(day) > sum(night) / len(night) + 2


def test_days_have_a_higher_vpd_than_nights():
    rows = synthetic_history(3, NOW)
    day = [r["vpd"] for r in rows if lights_on(r["timestamp"], 18.0)]
    night = [r["vpd"] for r in rows if not lights_on(r["timestamp"], 18.0)]
    assert sum(day) / len(day) > sum(night) / len(night)


def test_mostly_inside_the_stage_band_with_a_few_excursions():
    rows = synthetic_history(3, NOW)
    low, high = VPD_RANGES["early_veg"]
    inside = [low <= r["vpd"] <= high for r in rows]
    assert sum(inside) / len(rows) > 0.9
    assert not all(inside)


def test_same_seed_same_rows_and_another_seed_differs():
    assert synthetic_history(1, NOW, seed=3) == synthetic_history(1, NOW, seed=3)
    assert synthetic_history(1, NOW, seed=3) != synthetic_history(1, NOW, seed=4)


def test_rows_use_the_sensor_data_columns_and_a_timestamp():
    columns = {
        "temperature", "humidity", "vpd", "outside_temperature", "outside_humidity",
        "leaf_temperature", "leaf_vpd", "target_humidity", "temperature_min", "temperature_max",
        "humidity_min", "humidity_max", "sample_n", "stage", "indoor_source", "control_action",
        "quality", "temperature_raw", "humidity_raw", "outside_temperature_raw",
        "outside_humidity_raw",
    }
    for row in synthetic_history(1, NOW)[:20]:
        assert set(row) == columns | {"timestamp"}


def test_stage_changes_switch_the_band():
    change = NOW - 3600
    rows = synthetic_history(1, NOW, start_stage="early_veg", stage_changes=[(change, "late_veg")])
    assert {r["stage"] for r in rows if r["timestamp"] < change} == {"early_veg"}
    assert {r["stage"] for r in rows if r["timestamp"] >= change} == {"late_veg"}


def test_rejects_a_non_positive_span():
    with pytest.raises(ValueError):
        synthetic_history(0, NOW)


def test_guard_refuses_the_real_data_directory():
    with pytest.raises(ValueError):
        check_seed_path(DATA_DIR / "autocann.db", None)


def test_guard_refuses_a_path_outside_the_sandbox(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    with pytest.raises(ValueError):
        check_seed_path(tmp_path / "elsewhere.db", sandbox)


def test_guard_accepts_the_sandbox_or_an_explicit_path(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    assert check_seed_path(sandbox / "sim.db", sandbox) == (sandbox / "sim.db").resolve()
    assert check_seed_path(tmp_path / "explicit.db", None) == (tmp_path / "explicit.db").resolve()


def test_seed_writes_through_the_store_into_the_sandbox_only(tmp_path):
    db = tmp_path / "sim.db"
    with _sandbox_store(db):
        ensure_schema()
        grow_id = create_grow("sim", stage="early_veg")
    assert grow_id is not None
    plan = plan_history(4, NOW, gaps=default_gaps(4))
    seed_history(db, plan, sandbox_dir=tmp_path)

    with _sandbox_store(db):
        rows = get_sensor_data_range(grow_id=grow_id, valid_only=False)
        events = get_stage_events(grow_id)
        waterings = get_irrigations(grow_id)
        grow_start = next(g for g in get_all_grows() if g["id"] == grow_id)
    assert len(rows) == len(plan.rows)
    assert events[0]["started_at"] == plan.rows[0]["timestamp"]
    assert [e["stage"] for e in events] == ["early_veg", "late_veg"]
    assert len(waterings) == len(plan.irrigations)
    assert grow_start["stage"] == "late_veg"
    assert grow_start["start_date"] == datetime.fromtimestamp(
        plan.rows[0]["timestamp"], ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S")


def test_seed_refuses_a_path_outside_the_sandbox_before_writing(tmp_path):
    db = tmp_path / "outside.db"
    with pytest.raises(ValueError):
        seed_history(db, plan_history(1, NOW), sandbox_dir=tmp_path / "sandbox")
    assert not db.exists()


def test_backdate_moves_the_creation_event(tmp_path):
    db = tmp_path / "sim.db"
    with _sandbox_store(db):
        ensure_schema()
        grow_id = create_grow("sim", stage="early_veg")
        assert backdate_grow(grow_id, NOW - 86400, "early_veg")
        assert get_stage_events(grow_id)[0]["started_at"] == NOW - 86400
