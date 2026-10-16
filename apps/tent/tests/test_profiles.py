from __future__ import annotations

import sqlite3

import pytest

import autocann.db as db
from autocann.control.feeding import WATER_ONLY_STAGES
from autocann.control.profiles import validate_profile_stages
from autocann.control.vpd_math import (
    HUMIDITY_RANGES,
    STAGE_EXPECTED_DAYS,
    STAGE_NAMES,
    VPD_RANGES,
)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the database layer at a throwaway file."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "_schema_ready", False)
    db.ensure_schema()
    return db


def _stage(key="early_veg", **overrides):
    stage = {
        "key": key,
        "name": "Etapa",
        "expected_days": 10,
        "vpd_min": 0.6,
        "vpd_max": 1.0,
        "rh_min": None,
        "rh_max": None,
    }
    stage.update(overrides)
    return stage


def _builtin_profile(database):
    return database.get_profile(db.BUILTIN_PROFILE_ID)


def test_fresh_database_has_exactly_one_builtin_profile(temp_db):
    profiles = temp_db.get_profiles()
    assert profiles == [{"id": "cannabis-photoperiod", "name": "Cannabis fotoperiódico", "kind": "builtin"}]


def test_builtin_profile_stages_equal_the_constants_field_by_field(temp_db):
    profile = _builtin_profile(temp_db)
    stages = profile["stages"]

    assert [stage["key"] for stage in stages] == list(VPD_RANGES)
    for position, stage in enumerate(stages):
        key = stage["key"]
        assert stage["profile_id"] == db.BUILTIN_PROFILE_ID
        assert stage["position"] == position
        assert stage["name"] == STAGE_NAMES[key]
        assert stage["expected_days"] == STAGE_EXPECTED_DAYS[key]
        assert stage["vpd_min"] == VPD_RANGES[key][0]
        assert stage["vpd_max"] == VPD_RANGES[key][1]
        if key in HUMIDITY_RANGES:
            assert stage["rh_min"] == HUMIDITY_RANGES[key][0]
            assert stage["rh_max"] == HUMIDITY_RANGES[key][1]
        else:
            assert stage["rh_min"] is None
            assert stage["rh_max"] is None
        assert stage["water_only"] is (key in WATER_ONLY_STAGES)


def test_get_profile_returns_none_for_an_unknown_id(temp_db):
    assert temp_db.get_profile("does-not-exist") is None


def test_running_the_schema_init_twice_keeps_one_profile_with_the_same_stages(temp_db):
    before = _builtin_profile(temp_db)

    temp_db.init_database()

    assert len(temp_db.get_profiles()) == 1
    assert _builtin_profile(temp_db) == before


def test_a_manual_edit_survives_the_schema_init(temp_db):
    conn = temp_db._open()
    conn.execute(
        "UPDATE profile_stages SET name = 'Plántula editada' WHERE profile_id = ? AND key = 'seedling'",
        (db.BUILTIN_PROFILE_ID,),
    )
    conn.commit()
    conn.close()

    temp_db.init_database()

    stages = {stage["key"]: stage for stage in _builtin_profile(temp_db)["stages"]}
    assert stages["seedling"]["name"] == "Plántula editada"


def test_existing_database_without_the_new_tables_gets_them_on_init(temp_db):
    conn = temp_db._open()
    conn.execute("DROP TABLE profile_stages")
    conn.execute("DROP TABLE crop_profiles")
    conn.commit()
    conn.close()

    temp_db.init_database()

    assert [profile["id"] for profile in temp_db.get_profiles()] == [db.BUILTIN_PROFILE_ID]
    assert _builtin_profile(temp_db)["stages"]


def test_stages_are_returned_in_position_order(temp_db):
    stages = _builtin_profile(temp_db)["stages"]
    positions = [stage["position"] for stage in stages]
    assert positions == sorted(positions)


def test_crop_profiles_rejects_an_unknown_kind(temp_db):
    conn = temp_db._open()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO crop_profiles (id, name, kind) VALUES ('x', 'X', 'other')"
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# validate_profile_stages: one test per rule
# ---------------------------------------------------------------------------


def test_builtin_profile_validates_clean(temp_db):
    assert validate_profile_stages(_builtin_profile(temp_db)["stages"]) == []


def test_a_profile_needs_at_least_one_stage():
    errors = validate_profile_stages([])
    assert len(errors) == 1
    assert "at least one stage" in errors[0]


def test_stage_keys_must_be_unique():
    errors = validate_profile_stages([_stage("early_veg"), _stage("early_veg", expected_days=5)])
    assert any("'early_veg'" in error and "more than one stage" in error for error in errors)


def test_a_stage_needs_a_band():
    errors = validate_profile_stages([_stage(vpd_min=None, vpd_max=None)])
    assert any("needs a VPD band" in error for error in errors)


def test_a_vpd_band_needs_both_ends():
    errors = validate_profile_stages([_stage(vpd_max=None)])
    assert any("VPD band is incomplete" in error for error in errors)


def test_an_rh_band_needs_both_ends():
    errors = validate_profile_stages([_stage(vpd_min=None, vpd_max=None, rh_min=60.0)])
    assert any("RH band is incomplete" in error for error in errors)


def test_a_stage_may_carry_an_rh_band_alone():
    stage = _stage(vpd_min=None, vpd_max=None, rh_min=60.0, rh_max=65.0)
    assert validate_profile_stages([stage]) == []


def test_a_stage_may_carry_both_bands():
    stage = _stage(rh_min=60.0, rh_max=65.0)
    assert validate_profile_stages([stage]) == []


def test_vpd_band_must_be_ordered_and_non_negative():
    assert any("VPD band" in e for e in validate_profile_stages([_stage(vpd_min=1.0, vpd_max=1.0)]))
    assert any("VPD band" in e for e in validate_profile_stages([_stage(vpd_min=1.2, vpd_max=0.8)]))
    assert any("VPD band" in e for e in validate_profile_stages([_stage(vpd_min=-0.1, vpd_max=1.0)]))


def test_vpd_band_upper_limit_is_4_kpa():
    assert validate_profile_stages([_stage(vpd_min=1.0, vpd_max=4.0)]) == []
    errors = validate_profile_stages([_stage(vpd_min=1.0, vpd_max=4.5)])
    assert any("<= 4 kPa" in error for error in errors)


def test_rh_band_must_be_ordered_and_within_0_and_100():
    assert validate_profile_stages([_stage(vpd_min=None, vpd_max=None, rh_min=0.0, rh_max=100.0)]) == []
    assert any(
        "RH band" in e
        for e in validate_profile_stages([_stage(vpd_min=None, vpd_max=None, rh_min=65.0, rh_max=60.0)])
    )
    assert any(
        "RH band" in e
        for e in validate_profile_stages([_stage(vpd_min=None, vpd_max=None, rh_min=60.0, rh_max=101.0)])
    )
    assert any(
        "RH band" in e
        for e in validate_profile_stages([_stage(vpd_min=None, vpd_max=None, rh_min=-1.0, rh_max=65.0)])
    )


def test_open_ended_stage_is_allowed_only_last():
    open_last = [_stage("early_veg"), _stage("late_veg", expected_days=None)]
    assert validate_profile_stages(open_last) == []

    open_first = [_stage("early_veg", expected_days=None), _stage("late_veg")]
    errors = validate_profile_stages(open_first)
    assert any("only the last stage may be open-ended" in error for error in errors)


def test_expected_days_must_be_positive_when_set():
    errors = validate_profile_stages([_stage(expected_days=0)])
    assert any("expected_days must be positive" in error for error in errors)
    errors = validate_profile_stages([_stage(expected_days=-3)])
    assert any("expected_days must be positive" in error for error in errors)
