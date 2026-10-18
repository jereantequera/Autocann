"""
The dashboard and the API read stages from the active grow, not the constants.

The snapshot in fixtures/grow_stage_api_snapshot.json was captured from the
code before this change (a migrated tent: built-in profile, one grow). The
migrated tent must still answer with exactly those bytes. The custom profile
tests show a grow on another profile gets its own stages everywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import autocann.db as db
from autocann.store.grow_stages import grow_vpd_band

SNAPSHOT = Path(__file__).parent / "fixtures" / "grow_stage_api_snapshot.json"
CUSTOM_PROFILE = "custom-propagation"


def _execute(sql, params=()):
    conn = db._open()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _insert_custom_profile():
    """A custom profile with its own stage keys: no seedling, no flowering."""
    _execute(
        "INSERT INTO crop_profiles (id, name, kind, created_at) VALUES (?, ?, 'custom', ?)",
        (CUSTOM_PROFILE, "Esquejes", "2026-01-01 00:00:00"),
    )
    stages = [
        # key, name, position, expected_days, vpd_min, vpd_max, rh_min, rh_max, water_only
        ("propagation", "Esqueje", 0, 10, 0.3, 0.6, None, None, 1),
        ("bloom", "Floración corta", 1, 40, 1.1, 1.4, None, None, 0),
        ("dry_room", "Secado en cuarto", 2, None, None, None, 55.0, 60.0, 0),
    ]
    for key, name, position, days, vpd_min, vpd_max, rh_min, rh_max, water_only in stages:
        _execute(
            "INSERT INTO profile_stages (profile_id, key, name, position, expected_days, "
            "vpd_min, vpd_max, rh_min, rh_max, water_only) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (CUSTOM_PROFILE, key, name, position, days, vpd_min, vpd_max, rh_min, rh_max, water_only),
        )


# ---------------------------------------------------------------------------
# Migrated tent: byte-identical responses


def test_migrated_tent_responses_are_byte_identical_to_the_snapshot(client, temp_db):
    temp_db.create_grow("Carpa snapshot", "early_veg")
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    for path, recorded in expected.items():
        response = client.get(path)
        assert response.status_code == recorded["status"], path
        assert response.get_data(as_text=True) == recorded["body"], path


def test_without_an_active_grow_config_is_the_builtin_profile(client, temp_db):
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    temp_db.create_grow("Carpa snapshot", "early_veg")
    temp_db.end_grow(temp_db.get_active_grow()["id"])

    response = client.get("/api/config")

    assert response.get_data(as_text=True) == expected["/api/config"]["body"]


# ---------------------------------------------------------------------------
# A grow on a custom profile


def test_config_serves_the_custom_grows_stages(client, temp_db):
    _insert_custom_profile()
    temp_db.create_grow("Esquejes 1", "propagation", profile_id=CUSTOM_PROFILE)

    config = client.get("/api/config").get_json()

    assert config["stages"] == ["propagation", "bloom", "dry_room"]
    assert config["stage_names"] == {
        "propagation": "Esqueje",
        "bloom": "Floración corta",
        "dry_room": "Secado en cuarto",
    }
    assert config["stage_expected_days"] == {"propagation": 10, "bloom": 40, "dry_room": None}
    assert config["vpd_ranges"]["propagation"] == {"min": 0.3, "max": 0.6}
    assert config["vpd_ranges"]["bloom"] == {"min": 1.1, "max": 1.4}
    assert config["vpd_ranges"]["dry_room"] == {"min": None, "max": None}
    assert config["humidity_ranges"] == {
        "propagation": None,
        "bloom": None,
        "dry_room": {"min": 55.0, "max": 60.0},
    }


def test_vpd_band_uses_the_custom_stage_band(client, temp_db):
    _insert_custom_profile()
    temp_db.create_grow("Esquejes 1", "propagation", profile_id=CUSTOM_PROFILE)

    vpd = client.get("/api/vpd-band").get_json()
    assert vpd["stage"] == "propagation"
    assert vpd["kind"] == "vpd"
    assert (vpd["vpd_min"], vpd["vpd_max"]) == (0.3, 0.6)

    humidity = client.get("/api/vpd-band?stage=dry_room").get_json()
    assert humidity["kind"] == "humidity"
    assert all(p["humidity_low"] == 55.0 and p["humidity_high"] == 60.0 for p in humidity["points"])


def test_day_counters_and_timeline_use_the_custom_expected_days(temp_db):
    _insert_custom_profile()
    grow_id = temp_db.create_grow("Esquejes 1", "propagation", profile_id=CUSTOM_PROFILE)

    active = temp_db.get_active_grow()
    assert active["id"] == grow_id
    assert active["stage_expected_days"] == 10
    assert temp_db.get_stage_timeline(grow_id)[0]["expected_days"] == 10


def test_unknown_stage_scores_against_the_default_band(temp_db):
    grow_id = temp_db.create_grow("Carpa", "early_veg")

    assert grow_vpd_band(grow_id, "no_such_stage") == (0.6, 1.5)


def test_stage_validation_follows_the_grow_profile(client, temp_db):
    _insert_custom_profile()
    custom_grow = temp_db.create_grow("Esquejes 1", "propagation", profile_id=CUSTOM_PROFILE)

    accepted = client.put(f"/api/grows/{custom_grow}/stage", json={"stage": "bloom"})
    assert accepted.status_code == 200

    rejected = client.put(f"/api/grows/{custom_grow}/stage", json={"stage": "flowering"})
    assert rejected.status_code == 400
    assert rejected.get_json()["error"] == "Invalid stage. Use one of: propagation, bloom, dry_room"
    assert rejected.get_json()["code"] == "invalid_stage"


def test_new_grow_on_a_profile_validates_against_that_profile(client, temp_db):
    _insert_custom_profile()

    rejected = client.post("/api/grows", json={"name": "Cannabis", "stage": "seedling", "profile_id": CUSTOM_PROFILE})
    assert rejected.status_code == 400

    created = client.post("/api/grows", json={"name": "Esquejes", "stage": "bloom", "profile_id": CUSTOM_PROFILE})
    assert created.status_code == 201
    assert temp_db.get_active_grow()["profile_id"] == CUSTOM_PROFILE


def test_new_grow_without_a_profile_still_uses_the_builtin_stages(client, temp_db):
    response = client.post("/api/grows", json={"name": "Clásico"})

    assert response.status_code == 201
    assert temp_db.get_active_grow()["profile_id"] == temp_db.BUILTIN_PROFILE_ID


def test_new_grow_on_an_unknown_profile_is_rejected(client):
    response = client.post("/api/grows", json={"name": "Nada", "profile_id": "does-not-exist"})

    assert response.status_code == 400
