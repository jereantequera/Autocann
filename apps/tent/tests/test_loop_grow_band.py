"""
The control loop takes its band from the active grow's stages, not the constants.

The migrated built-in profile must give exactly today's setpoints; a grow on a
custom profile must use its own band; a failing database keeps the last good
band; an unknown stage key has no band.
"""

from __future__ import annotations

import logging

import pytest

import autocann.db as db
import autocann.runtime.stage_band as stage_band_module
from autocann.control.vpd_math import STAGES
from autocann.runtime.setpoint import resolve_setpoint
from autocann.runtime.stage_band import StageBand
from autocann.store.grow_stages import stage_table

TEMPERATURES = [18.0, 20.0, 22.0, 24.0, 26.0, 28.0, 30.0, 32.0]
HUMIDITIES = [30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]
CUSTOM_PROFILE = "custom-loop-band"


def _execute(sql, params=()):
    conn = db._open()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _insert_custom_profile():
    """Custom stages: a VPD band of its own, an RH band, and a `dry` that is VPD-driven."""
    _execute(
        "INSERT INTO crop_profiles (id, name, kind, created_at) VALUES (?, ?, 'custom', ?)",
        (CUSTOM_PROFILE, "Banda propia", "2026-01-01 00:00:00"),
    )
    stages = [
        # key, name, position, expected_days, vpd_min, vpd_max, rh_min, rh_max, water_only
        ("propagation", "Esqueje", 0, 10, 0.3, 0.6, None, None, 1),
        ("bloom", "Floración corta", 1, 40, 1.1, 1.4, None, None, 0),
        ("dry_room", "Secado en cuarto", 2, None, None, None, 55.0, 60.0, 0),
        ("dry", "Secado con VPD", 3, 5, 0.9, 1.1, None, None, 0),
    ]
    for key, name, position, days, vpd_min, vpd_max, rh_min, rh_max, water_only in stages:
        _execute(
            "INSERT INTO profile_stages (profile_id, key, name, position, expected_days, "
            "vpd_min, vpd_max, rh_min, rh_max, water_only) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (CUSTOM_PROFILE, key, name, position, days, vpd_min, vpd_max, rh_min, rh_max, water_only),
        )


def _band_of(table, key):
    row = table[key]
    return {k: row[k] for k in ("vpd_min", "vpd_max", "rh_min", "rh_max")}


@pytest.mark.parametrize("stage", STAGES)
def test_migrated_builtin_band_gives_todays_setpoints(temp_db, stage):
    grow_id = temp_db.create_grow("Carpa", stage)
    band = _band_of(stage_table(grow_id), stage)

    for temperature in TEMPERATURES:
        for humidity in HUMIDITIES:
            with_band = resolve_setpoint(stage, temperature, humidity, band=band)
            assert with_band == resolve_setpoint(stage, temperature, humidity), (stage, temperature, humidity)


def test_custom_vpd_band_is_used_not_the_constant(temp_db):
    _insert_custom_profile()
    grow_id = temp_db.create_grow("Esquejes", "propagation", profile_id=CUSTOM_PROFILE)
    band = _band_of(stage_table(grow_id), "propagation")

    setpoint = resolve_setpoint("propagation", 24.0, 60.0, band=band)

    # The custom 0.3-0.6 kPa band at this air, judged at the leaf.
    expected = resolve_setpoint("propagation", 24.0, 60.0, band={"vpd_min": 0.3, "vpd_max": 0.6})
    assert setpoint == expected
    assert setpoint.vpd_in_range == (0.3 <= setpoint.leaf_vpd <= 0.6)


def test_custom_rh_band_wins_over_the_built_in_one(temp_db):
    _insert_custom_profile()
    grow_id = temp_db.create_grow("Secado", "dry_room", profile_id=CUSTOM_PROFILE)
    band = _band_of(stage_table(grow_id), "dry_room")

    setpoint = resolve_setpoint("dry_room", 22.0, 50.0, band=band)

    assert setpoint.humidity_band == (55.0, 60.0)
    assert setpoint.target_humidity == 57.5
    assert setpoint.vpd_in_range is False


def test_custom_dry_stage_with_a_vpd_band_does_not_inherit_the_built_in_rh_band(temp_db):
    _insert_custom_profile()
    grow_id = temp_db.create_grow("Secado VPD", "dry", profile_id=CUSTOM_PROFILE)
    band = _band_of(stage_table(grow_id), "dry")

    setpoint = resolve_setpoint("dry", 24.0, 60.0, band=band)

    assert setpoint.humidity_band != (60.0, 65.0)
    assert setpoint == resolve_setpoint("dry", 24.0, 60.0, band={"vpd_min": 0.9, "vpd_max": 1.1})


def test_unknown_stage_key_has_no_band(temp_db):
    grow_id = temp_db.create_grow("Carpa", "early_veg")
    loop_band = StageBand()
    loop_band.refresh(grow_id)

    assert loop_band.get("no_such_stage") is None
    assert loop_band.get("early_veg") is not None


def test_a_grow_without_a_snapshot_is_judged_against_the_builtin_profile(temp_db):
    loop_band = StageBand()

    table = loop_band.refresh(999_999)

    assert set(table) == set(STAGES)


def test_a_failing_database_keeps_the_last_good_band(temp_db, monkeypatch, caplog):
    grow_id = temp_db.create_grow("Carpa", "late_veg")
    loop_band = StageBand()
    good = loop_band.refresh(grow_id)
    before = loop_band.get("late_veg")

    def broken(_grow_id):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(stage_band_module, "stage_table", broken)
    with caplog.at_level(logging.WARNING, logger=stage_band_module.log.name):
        for _ in range(3):
            assert loop_band.refresh(grow_id) is good

    assert loop_band.get("late_veg") == before
    failures = [r for r in caplog.records if "Cannot read the stages" in r.getMessage()]
    assert len(failures) == 1


def test_the_band_recovers_when_the_database_does(temp_db, monkeypatch):
    grow_id = temp_db.create_grow("Carpa", "flowering")
    loop_band = StageBand()
    loop_band.refresh(grow_id)

    monkeypatch.setattr(stage_band_module, "stage_table", lambda _id: 1 / 0)
    loop_band.refresh(grow_id)
    monkeypatch.undo()

    assert loop_band.refresh(grow_id) == stage_table(grow_id)
