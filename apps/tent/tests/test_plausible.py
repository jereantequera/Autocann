"""
Lecturas imposibles: no se aceptan, no se usan, no se guardan y no se cuentan.

Antes eran una alerta ("Temperatura inválida") que nadie podía resolver. Ahora
una lectura fuera de -10..60 °C o 0..100 % es una lectura fallida: el endpoint
la rechaza, el lazo la trata como una lectura que no llegó, y las estadísticas
saltean las filas viejas que la tienen, sin reescribir la base.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from autocann.cli.vpd import Sensors
from autocann.control import plausible
from autocann.control.plausible import plausible_reading
from autocann.time import ARGENTINA_TZ


@pytest.mark.parametrize("temperature, humidity, ok", [
    (24.0, 60.0, True),
    (-10.0, 0.0, True),
    (60.0, 100.0, True),
    (None, 60.0, True),       # missing is not impossible
    (85.0, 60.0, False),
    (-40.0, 60.0, False),
    (24.0, 140.0, False),
    (24.0, -1.0, False),
])
def test_what_a_tent_sensor_can_report(temperature, humidity, ok):
    assert plausible_reading(temperature, humidity) is ok


# ---------------------------------------------------------------------------
# At the source
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", [
    {"temperature": 70.0, "humidity": 60.0},    # accepted before: the range was -40..80
    {"temperature": -20.0, "humidity": 60.0},
    {"temperature": 24.0, "humidity": 101.0},
])
def test_the_endpoint_refuses_an_impossible_reading_and_keeps_the_last_good_one(client, payload):
    assert client.post("/api/sensor/indoor", json={"temperature": 24.0, "humidity": 60.0}).status_code == 200
    response = client.post("/api/sensor/indoor", json=payload)
    assert response.status_code == 400
    assert "expected" in response.get_json()["error"]
    assert client.get("/api/sensor/indoor").get_json()["temperature"] == 24.0


class _FakeRedis:
    def __init__(self, payload):
        self.payload = payload

    def get(self, key):
        import json
        return json.dumps(self.payload)


def test_the_loop_treats_an_impossible_esp32_reading_as_a_failed_read(monkeypatch):
    now = int(datetime.now(ARGENTINA_TZ).timestamp())
    sensors = Sensors(use_esp32_indoor=True)
    monkeypatch.setattr("autocann.cli.vpd.redis_client",
                        _FakeRedis({"temperature": 80.0, "humidity": 60.0, "timestamp": now}))
    assert sensors._read_esp32_key("esp32_indoor", "indoor") == (None, None)

    monkeypatch.setattr("autocann.cli.vpd.redis_client",
                        _FakeRedis({"temperature": 24.0, "humidity": 60.0, "timestamp": now}))
    assert sensors._read_esp32_key("esp32_indoor", "indoor") == (24.0, 60.0)


def test_the_median_keeps_the_last_good_value_and_the_failsafe_still_ages(monkeypatch):
    """Una lectura descartada no llega al control ni al acumulador."""
    sensors = Sensors(use_esp32_indoor=True)
    monkeypatch.setattr(sensors, "_read_dht22", lambda *a, **k: (None, None))
    monkeypatch.setattr(sensors, "_read_esp32_indoor", lambda: (24.0, 60.0))
    assert sensors.read()["temperature"] == 24.0
    seen = sensors.last_reading_at

    monkeypatch.setattr(sensors, "_read_esp32_indoor", lambda: (None, None))  # what 85 °C becomes
    assert sensors.read()["temperature"] == 24.0
    assert sensors.last_reading_at == seen


class _FakeDHT:
    temperature = -40.0
    humidity = 60.0


def test_a_local_dht22_railed_at_minus_40_is_a_failed_read(monkeypatch):
    monkeypatch.setattr("autocann.cli.vpd.DHT_READ_ATTEMPTS", 2)
    monkeypatch.setattr("autocann.cli.vpd.DHT_RETRY_DELAY_SECONDS", 0)
    assert Sensors()._read_dht22(_FakeDHT(), "outdoor") == (None, None)


# ---------------------------------------------------------------------------
# Rows stored before: every statistic skips them
# ---------------------------------------------------------------------------


def _insert(temp_db, rows):
    grow_id = temp_db.get_active_grow()["id"]
    conn = temp_db._open()
    for ts, t, h, t_max in rows:
        conn.execute(
            "INSERT INTO sensor_data (grow_id, timestamp, datetime, temperature, humidity, vpd,"
            " temperature_min, temperature_max, humidity_min, humidity_max, sample_n)"
            " VALUES (?, ?, ?, ?, ?, 1.0, ?, ?, ?, ?, 60)",
            (grow_id, ts, datetime.fromtimestamp(ts, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
             t, h, t, t_max, h, h),
        )
    conn.commit()
    conn.close()


#: A Wednesday at 12:00, so the week, the hour bucket and the day hold every row.
NOW = int(ARGENTINA_TZ.localize(datetime(2026, 10, 14, 12, 0)).timestamp())


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.fromtimestamp(NOW, tz)


@pytest.fixture
def mixed(temp_db, monkeypatch):
    """Four good rows at 24 °C / 60 %, and three broken ones of each kind."""
    monkeypatch.setattr(temp_db, "datetime", _Frozen)
    now = NOW - 60
    _insert(temp_db, [
        (now - 0 * 300, 24.0, 60.0, 24.0),
        (now - 1 * 300, 85.0, 60.0, 85.0),     # a failed read stored as the average
        (now - 2 * 300, 24.0, 60.0, 24.0),
        (now - 3 * 300, 24.0, 140.0, 24.0),    # impossible humidity
        (now - 4 * 300, 24.0, 60.0, 24.0),
        (now - 5 * 300, 24.5, 60.0, 80.0),     # a failed read hidden in the envelope
        (now - 6 * 300, 24.0, 60.0, 24.0),
    ])
    return temp_db, now


def test_the_hourly_aggregate_skips_impossible_rows(mixed):
    temp_db, now = mixed
    buckets = temp_db.get_aggregated_data(now - 3600, now, 3600)
    assert len(buckets) == 1
    bucket = buckets[0]
    assert bucket["sample_count"] == 4
    assert bucket["temperature"] == 24.0
    assert bucket["max_temperature"] == 24.0
    assert bucket["temperature_max"] == 24.0
    assert bucket["humidity"] == 60.0
    assert bucket["max_humidity"] == 60.0


def test_the_period_summary_skips_impossible_rows(mixed):
    temp_db, now = mixed
    summary = temp_db.get_period_summary(now - 3600, now)
    assert summary["sample_count"] == 4
    assert summary["temperature"] == {"avg": 24.0, "min": 24.0, "max": 24.0}
    assert summary["humidity"]["max"] == 60.0


def test_the_vpd_score_counts_only_real_samples(mixed):
    temp_db, now = mixed
    score = temp_db.get_vpd_score(start_ts=now - 3600, end_ts=now)
    assert score["period_samples_total"] == 4
    assert sum(day["samples_total"] for day in score["daily_scores"]) == 4


def test_the_weekly_report_skips_impossible_rows(mixed):
    temp_db, _ = mixed
    report = temp_db.get_weekly_report()
    assert report["summary"]["sample_count"] == 4
    assert report["summary"]["temperature"]["max"] == 24.0
    hourly = report["hourly_distribution"]
    assert sum(h["samples"] for h in hourly) == 4
    assert all(h["avg_temp"] == 24.0 for h in hourly)


def test_the_chart_rows_skip_impossible_rows_but_the_recorder_age_does_not(mixed):
    temp_db, now = mixed
    rows = temp_db.get_sensor_data_range(now - 3600, now)
    assert len(rows) == 4
    assert all(r["temperature"] == 24.0 for r in rows)
    # "Is the loop still writing?" is about rows existing, broken or not.
    assert len(temp_db.get_sensor_data_range(now - 3600, now, valid_only=False)) == 7


def test_old_impossible_rows_raise_no_alert(mixed):
    temp_db, _ = mixed
    report = temp_db.detect_anomalies(hours=24)
    assert report["status"] == "ok", report


def test_the_limits_are_the_same_everywhere():
    assert f"BETWEEN {plausible.TEMPERATURE_MIN_C} AND {plausible.TEMPERATURE_MAX_C}" in plausible.VALID_ROW_SQL
    assert f"BETWEEN {plausible.HUMIDITY_MIN_PERCENT} AND {plausible.HUMIDITY_MAX_PERCENT}" in plausible.VALID_ROW_SQL
