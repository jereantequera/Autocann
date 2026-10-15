from __future__ import annotations

import sqlite3
import time

import pytest

import autocann.cli.vpd as cli_vpd
import autocann.control.vpd_math as vpd_math
import autocann.web.app as web
from autocann.control.vpd_math import (
    LEAF_TEMP_OFFSET_C,
    STAGES,
    VPD_RANGES,
    humidity_range_bounds_for_stage,
)

# Fixtures `client` and `temp_db` come from conftest.py.

DAY = 86400

# Captured at collection time, before any test runs: tests/test_db.py reloads
# vpd_math later on, which would give a fresh object and say nothing about
# whether the two consumers share one.
_LEAF_AT_IMPORT = vpd_math.LEAF_TEMP_OFFSET_C


def _control_event(db, timestamp, event_type, value):
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        "INSERT INTO control_events (timestamp, datetime, event_type, value) VALUES (?, ?, ?, ?)",
        (timestamp, "2000-01-01 00:00:00", event_type, value),
    )
    conn.commit()
    conn.close()


def _set_columns(db, table, row_id, column, value):
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (value, row_id))
    conn.commit()
    conn.close()


def test_leaf_offset_is_one_object_shared_by_cli_and_web():
    # Two copies of the offset would let the chart and the loop drift apart
    # without any test noticing, so both sides must hold the same object.
    assert cli_vpd.LEAF_TEMP_OFFSET_C is _LEAF_AT_IMPORT
    assert web.LEAF_TEMP_OFFSET_C is _LEAF_AT_IMPORT
    assert _LEAF_AT_IMPORT == 1.5


def test_vpd_band_has_29_points_and_low_is_below_high(client):
    body = client.get("/api/vpd-band?stage=early_veg").get_json()

    assert len(body["points"]) == 29
    assert body["points"][0]["temperature"] == 18.0
    assert body["points"][-1]["temperature"] == 32.0
    assert body["kind"] == "vpd"
    assert (body["vpd_min"], body["vpd_max"]) == VPD_RANGES["early_veg"]
    assert body["leaf_offset_c"] == LEAF_TEMP_OFFSET_C
    for point in body["points"]:
        assert point["humidity_low"] < point["humidity_high"]


def test_vpd_band_matches_what_the_controller_computes(client):
    body = client.get("/api/vpd-band?stage=late_veg").get_json()
    point = next(p for p in body["points"] if p["temperature"] == 24.0)

    low, high = sorted(
        humidity_range_bounds_for_stage("late_veg", round(24.0 - LEAF_TEMP_OFFSET_C, 1))
    )
    assert point["humidity_low"] == round(low, 1)
    assert point["humidity_high"] == round(high, 1)

    # The same statement through the controller's own entry point: the band the
    # loop holds the tent to at this air temperature.
    setpoint = cli_vpd.resolve_setpoint("late_veg", 24.0, 50.0)
    assert point["humidity_low"] == round(setpoint.humidity_band[0], 1)
    assert point["humidity_high"] == round(setpoint.humidity_band[1], 1)


def test_dry_stage_returns_a_humidity_band_not_a_vpd_one(client):
    body = client.get("/api/vpd-band?stage=dry").get_json()

    assert body["kind"] == "humidity"
    assert body["vpd_min"] is None and body["vpd_max"] is None
    assert len(body["points"]) == 29
    assert all(p["humidity_low"] == 60.0 and p["humidity_high"] == 65.0 for p in body["points"])


def test_vpd_band_defaults_to_the_active_grow_stage(client, temp_db):
    temp_db.create_grow("Carpa 1", "flowering")

    body = client.get("/api/vpd-band").get_json()
    assert body["stage"] == "flowering"


def test_vpd_band_rejects_an_unknown_stage(client):
    response = client.get("/api/vpd-band?stage=cosecha")

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_every_stage_gets_a_band(client):
    for stage in STAGES:
        response = client.get(f"/api/vpd-band?stage={stage}")
        assert response.status_code == 200, stage


def test_config_serves_expected_days_for_every_stage(client):
    """
    The stage proportions bar in Cultivo draws each stage against its expected
    length. A stage missing here draws with no reference, and the bar says
    nothing about how far along the grow is.
    """
    payload = client.get("/api/config").get_json()
    expected = payload["stage_expected_days"]
    assert set(expected) == set(STAGES)
    for stage in STAGES:
        days = expected[stage]
        assert isinstance(days, int) and not isinstance(days, bool), stage
        assert days > 0, stage


def test_logbook_merges_sources_newest_first_in_spanish(client, temp_db):
    now = int(time.time())
    grow_id = temp_db.create_grow("Carpa 1", "early_veg")
    # The grow began two days ago: its own stage event is the one that says "Empezó".
    _set_columns(temp_db, "stage_events", _first_stage_event_id(temp_db, grow_id), "started_at", now - 2 * DAY)
    temp_db.record_stage_event(grow_id, "late_veg", started_at=now - 50)

    _control_event(temp_db, now - 100, "humidity_up", "on")
    _control_event(temp_db, now - 200, "ventilation", "off")
    _control_event(temp_db, now - 300, "humidity_down", "on")
    _control_event(temp_db, now - 400, "pause", "on")

    irrigation_id = temp_db.record_irrigation(
        grow_id, 10.0, [{"name": "Bio Grow", "unit": "ml/L", "dose": 2}], ph=6.0, ec=1.8,
    )
    _set_columns(temp_db, "irrigations", irrigation_id, "timestamp", now - 150)

    items = client.get("/api/logbook").get_json()["items"]
    timestamps = [item["timestamp"] for item in items]
    assert timestamps == sorted(timestamps, reverse=True)

    by_text = {item["text"]: item for item in items}
    assert by_text["Se prendió el humidificador"]["kind"] == "salida"
    assert by_text["Se apagó la extracción"]["kind"] == "salida"
    assert by_text["Se prendió el deshumidificador"]["kind"] == "salida"
    assert by_text["Riego · 10 L"]["kind"] == "riego"
    assert by_text["Riego · 10 L"]["detail"] == "Bio Grow 2 ml/L · pH 6,0 · EC 1,8"
    assert by_text["Pasó a Vegetativo Tardío"]["kind"] == "cultivo"
    assert by_text["Empezó Carpa 1 en Vegetativo Temprano"]["kind"] == "cultivo"
    assert by_text["Se pausó el control"]["kind"] == "sistema"
    assert set(by_text["Se prendió el humidificador"]) == {"timestamp", "datetime", "kind", "text", "detail"}


def test_logbook_respects_days_and_limit(client, temp_db):
    now = int(time.time())
    _control_event(temp_db, now - 10 * DAY, "humidity_down", "on")
    _control_event(temp_db, now - 100, "humidity_up", "on")
    _control_event(temp_db, now - 200, "humidity_up", "off")

    # The schema seeds an active grow whose start lands in the window too, so
    # only the relay rows are counted here.
    in_week = _relay_rows(client.get("/api/logbook?days=7").get_json()["items"])
    assert [i["text"] for i in in_week] == ["Se prendió el humidificador", "Se apagó el humidificador"]

    in_month = _relay_rows(client.get("/api/logbook?days=30").get_json()["items"])
    assert len(in_month) == 3

    capped = client.get("/api/logbook?days=30&limit=2").get_json()["items"]
    # Newest first: the seeded grow's start is "now", then the newest relay row.
    assert len(capped) == 2
    assert capped[1]["text"] == "Se prendió el humidificador"


def test_logbook_survives_an_unknown_event_type(client, temp_db):
    now = int(time.time())
    _control_event(temp_db, now - 60, "fan_speed", "73")

    response = client.get("/api/logbook")

    assert response.status_code == 200
    item = _relay_rows(response.get_json()["items"])[0]
    assert item["kind"] == "sistema"
    assert "fan_speed" in item["text"]


def _relay_rows(items):
    return [item for item in items if item["kind"] in ("salida", "sistema")]


def _first_stage_event_id(db, grow_id):
    conn = sqlite3.connect(db.DB_PATH)
    row_id = conn.execute(
        "SELECT id FROM stage_events WHERE grow_id = ? ORDER BY id ASC LIMIT 1", (grow_id,)
    ).fetchone()[0]
    conn.close()
    return row_id
