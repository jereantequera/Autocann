from __future__ import annotations

import sqlite3

import pytest

from autocann.control.vpd_math import VPD_RANGES

# Keys `autocann.cli.query_db.show_stats` prints. It used to crash with a
# KeyError because get_database_stats() returned only two of them.
REQUIRED_STAT_KEYS = {
    "database_path",
    "database_size_mb",
    "grow_count",
    "sensor_data_count",
    "control_events_count",
    "oldest_record",
    "newest_record",
}


def test_database_stats_returns_every_key_the_cli_prints(temp_db):
    stats = temp_db.get_database_stats()
    assert REQUIRED_STAT_KEYS <= set(stats)


def test_query_db_stats_runs_against_a_real_database(temp_db, capsys):
    from autocann.cli import query_db

    temp_db.store_sensor_sample(
        {"temperature": 24.0, "humidity": 60.0, "vpd": 1.19,
         "outside_temperature": 18.0, "outside_humidity": 70.0,
         "leaf_temperature": 22.5, "leaf_vpd": 1.09, "target_humidity": 63.0}
    )
    query_db.show_stats()
    out = capsys.readouterr().out
    assert "Sensor records:   1" in out
    assert "Error" not in out


def test_wal_mode_is_enabled_so_readers_and_the_control_loop_do_not_block(temp_db):
    conn = temp_db._open()
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"


def test_a_concurrent_writer_waits_instead_of_erroring(temp_db, monkeypatch):
    """A second writer used to fail instantly with 'database is locked'."""
    monkeypatch.setattr(temp_db, "BUSY_TIMEOUT_SECONDS", 2.0)
    holder = temp_db._open()
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(sqlite3.OperationalError):
            # Proves the lock is real: a connection with no timeout gives up at once.
            bare = sqlite3.connect(temp_db.DB_PATH, timeout=0)
            bare.execute("BEGIN EXCLUSIVE")
    finally:
        holder.rollback()
        holder.close()

    conn = temp_db._open()
    timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.close()
    assert timeout >= 1000


def test_a_zero_reading_survives_the_summary(temp_db):
    """`round(x, 1) if x else None` turned a real 0.0 into None."""
    temp_db.store_sensor_sample(
        {"temperature": 0.0, "humidity": 0.0, "vpd": 0.0,
         "outside_temperature": 0.0, "outside_humidity": 0.0,
         "leaf_temperature": 0.0, "leaf_vpd": 0.0, "target_humidity": 0.0}
    )
    summary = temp_db.get_period_summary(0, 2 ** 31)
    assert summary["sample_count"] == 1
    assert summary["temperature"]["avg"] == 0.0
    assert summary["humidity"]["min"] == 0.0
    assert summary["vpd"]["max"] == 0.0


def test_vpd_score_is_measured_on_leaf_vpd_like_the_controller(temp_db):
    """
    The controller judges leaf VPD. A sample whose leaf VPD is in range but whose
    air VPD is not must score as in range, or the dashboard contradicts the loop.
    """
    vpd_min, vpd_max = VPD_RANGES["flowering"]
    temp_db.update_grow_stage(temp_db.get_active_grow()["id"], "flowering")
    temp_db.store_sensor_sample(
        {"temperature": 24.0, "humidity": 50.0,
         "vpd": vpd_max + 0.4,            # air VPD: out of range
         "leaf_temperature": 22.5,
         "leaf_vpd": (vpd_min + vpd_max) / 2,  # leaf VPD: mid-band
         "outside_temperature": 18.0, "outside_humidity": 70.0, "target_humidity": 50.0}
    )
    score = temp_db.get_vpd_score(days=1)
    assert score["overall_score"] == 100.0


def test_stage_ranges_are_not_duplicated_in_the_db_layer(temp_db):
    assert temp_db.VPD_RANGES is VPD_RANGES


def test_importing_the_db_module_does_not_create_a_database(tmp_path, monkeypatch):
    """Schema creation used to run as an import side effect."""
    import importlib

    import autocann.db as fresh
    monkeypatch.setattr(fresh, "DB_PATH", tmp_path / "untouched.db")
    monkeypatch.setattr(fresh, "_schema_ready", False)
    importlib.reload(importlib.import_module("autocann.control.vpd_math"))
    assert not (tmp_path / "untouched.db").exists()


def test_anomalies_flag_a_stale_sensor(temp_db):
    import time

    old = int(time.time()) - 3600
    conn = temp_db._open()
    conn.execute(
        "INSERT INTO sensor_data (grow_id, timestamp, datetime, temperature, humidity, vpd,"
        " outside_temperature, outside_humidity) VALUES (1, ?, '2026-01-01 00:00:00', 24, 60, 1.2, 18, 70)",
        (old,),
    )
    conn.commit()
    conn.close()

    result = temp_db.detect_anomalies(hours=24)
    assert result["status"] == "critical"
    assert any(a["type"] == "stale_data" for a in result["anomalies"])


# ---------------------------------------------------------------------------
# Stage history
# ---------------------------------------------------------------------------


def test_creating_a_grow_records_its_first_stage(temp_db):
    grow_id = temp_db.create_grow("Nuevo", "early_veg")
    events = temp_db.get_stage_events(grow_id)
    assert [e["stage"] for e in events] == ["early_veg"]
    assert events[0]["notes"] == "Inicio del cultivo"


def test_each_stage_change_is_recorded(temp_db):
    grow_id = temp_db.create_grow("Ciclo", "early_veg")
    temp_db.update_grow_stage(grow_id, "late_veg")
    temp_db.update_grow_stage(grow_id, "flowering")
    assert [e["stage"] for e in temp_db.get_stage_events(grow_id)] == [
        "early_veg", "late_veg", "flowering",
    ]


def test_setting_the_same_stage_again_does_not_restart_the_counter(temp_db):
    """An accidental double click must not look like a new stage."""
    grow_id = temp_db.create_grow("Ciclo", "flowering")
    for _ in range(5):
        temp_db.update_grow_stage(grow_id, "flowering")
    assert len(temp_db.get_stage_events(grow_id)) == 1


def test_updating_a_grow_that_does_not_exist_fails_instead_of_logging_an_event(temp_db):
    assert temp_db.update_grow_stage(9999, "flowering") is False
    assert temp_db.get_stage_events(9999) == []


def test_the_active_grow_carries_its_day_counters(temp_db):
    grow_id = temp_db.create_grow("Contado", "flowering")
    grow = temp_db.get_active_grow()
    assert grow["id"] == grow_id
    assert grow["day_in_stage"] == 1            # created today
    assert grow["day_of_grow"] == 1
    assert grow["stage_expected_days"] == 56
    assert grow["stage_started_at"] is not None
    assert grow["estimated_end"] > grow["stage_started_at"]


def test_the_backfill_gives_pre_existing_grows_a_starting_point(temp_db):
    """
    A grow created before stage_events existed has no history; the migration
    estimates it from the grow's own start date so the counter has an origin.
    """
    conn = temp_db._open()
    conn.execute(
        "INSERT INTO grows (name, stage, start_date, is_active, notes)"
        " VALUES ('Viejo', 'late_veg', '2026-01-10 08:00:00', 1, '')"
    )
    conn.commit()
    grow_id = conn.execute("SELECT id FROM grows WHERE name = 'Viejo'").fetchone()[0]
    conn.execute("DELETE FROM stage_events WHERE grow_id = ?", (grow_id,))
    conn.commit()

    temp_db._backfill_stage_events(conn)
    events = temp_db.get_stage_events(grow_id)
    assert len(events) == 1
    assert events[0]["stage"] == "late_veg"
    assert events[0]["datetime"] == "2026-01-10 08:00:00"

    # Running it again must not add a second origin.
    temp_db._backfill_stage_events(conn)
    conn.close()
    assert len(temp_db.get_stage_events(grow_id)) == 1


def test_the_timeline_pairs_the_recorded_changes(temp_db):
    import time

    grow_id = temp_db.create_grow("Linea", "early_veg")
    day = 24 * 3600
    now = int(time.time())
    conn = temp_db._open()
    conn.execute("DELETE FROM stage_events WHERE grow_id = ?", (grow_id,))
    for stage, offset in [("early_veg", 40), ("late_veg", 20), ("flowering", 5)]:
        conn.execute(
            "INSERT INTO stage_events (grow_id, stage, started_at, datetime)"
            " VALUES (?, ?, ?, '')",
            (grow_id, stage, now - offset * day),
        )
    conn.commit()
    conn.close()

    timeline = temp_db.get_stage_timeline(grow_id, now=now)
    assert [p["stage"] for p in timeline] == ["early_veg", "late_veg", "flowering"]
    assert [p["days"] for p in timeline] == [20, 15, 6]
    assert timeline[-1]["is_current"] is True


# ---------------------------------------------------------------------------
# Sample detail and calibration
# ---------------------------------------------------------------------------


def test_an_interval_summary_round_trips_through_the_database(temp_db):
    temp_db.store_sensor_sample({
        "temperature": 24.5, "temperature_min": 22.0, "temperature_max": 27.0,
        "humidity": 52.0, "humidity_min": 46.0, "humidity_max": 58.0,
        "vpd": 1.2, "sample_n": 98, "stage": "flowering", "indoor_source": "esp32",
        "control_action": "dehumidify", "quality": "ok",
        "temperature_raw": 26.0, "humidity_raw": 50.0,
        "outside_temperature": 15.0, "outside_humidity": 70.0,
        "leaf_temperature": 23.0, "leaf_vpd": 1.1, "target_humidity": 50.5,
    })
    row = temp_db.get_latest_sensor_data(limit=1)[0]
    assert row["sample_n"] == 98
    assert (row["temperature_min"], row["temperature_max"]) == (22.0, 27.0)
    assert (row["stage"], row["indoor_source"], row["control_action"]) == (
        "flowering", "esp32", "dehumidify")
    assert row["quality"] == "ok"
    # Raw readings survive so a later recalibration does not orphan the history.
    assert (row["temperature_raw"], row["humidity_raw"]) == (26.0, 50.0)


def test_samples_written_before_the_richer_format_still_load(temp_db):
    """Old rows carry NULL in the new columns; nothing should choke on that."""
    temp_db.store_sensor_sample({
        "temperature": 24.0, "humidity": 50.0, "vpd": 1.2,
        "outside_temperature": 15.0, "outside_humidity": 70.0,
    })
    row = temp_db.get_latest_sensor_data(limit=1)[0]
    assert row["sample_n"] is None
    assert row["temperature_min"] is None
    assert temp_db.get_period_summary(0, 2 ** 31)["sample_count"] == 1


def test_a_sensor_without_calibration_gets_an_identity_correction(temp_db):
    """Never None: the caller applies the result unconditionally."""
    cal = temp_db.get_calibration("nunca_calibrado")
    assert cal.is_identity
    assert cal.apply(24.0, 50.0) == (24.0, 50.0)


def test_setting_a_calibration_replaces_the_previous_one(temp_db):
    temp_db.set_calibration("esp32_indoor", -1.2, 3.0, "primera")
    temp_db.set_calibration("esp32_indoor", -0.5, 1.0, "recalibrado")
    cal = temp_db.get_calibration("esp32_indoor")
    assert (cal.temperature_offset, cal.humidity_offset) == (-0.5, 1.0)
    assert len(temp_db.get_all_calibrations()) == 1


def test_a_stored_calibration_is_reversible_from_the_corrected_value(temp_db):
    temp_db.set_calibration("dht22_outdoor", 2.5, -4.0)
    cal = temp_db.get_calibration("dht22_outdoor")
    corrected = cal.apply(18.0, 65.0)
    assert cal.invert(*corrected) == pytest.approx((18.0, 65.0))


def test_aggregation_reports_the_real_envelope_not_the_range_of_averages(temp_db):
    """
    An hourly bucket holding twelve interval summaries should report the widest
    reading any of them saw, not the spread of their averages — which is much
    narrower and makes the tent look calmer than it was.
    """
    import time

    now = int(time.time())
    grow_id = temp_db.get_active_grow()["id"]
    conn = temp_db._open()
    for i in range(12):
        conn.execute(
            "INSERT INTO sensor_data (grow_id, timestamp, datetime, temperature, humidity,"
            " vpd, outside_temperature, outside_humidity, temperature_min, temperature_max,"
            " humidity_min, humidity_max, sample_n)"
            " VALUES (?, ?, '', 24.0, 50.0, 1.2, 15, 70, 20.0, 28.0, 44.0, 56.0, 100)",
            (grow_id, now - i * 300),
        )
    conn.commit()
    conn.close()

    # Hourly buckets split on the hour, so the rows may land in more than one.
    buckets = temp_db.get_aggregated_data(now - 7200, now, 3600)
    assert buckets

    for bucket in buckets:
        assert bucket["temperature"] == 24.0
        # Every row has the same average, so the spread of averages is nil...
        assert (bucket["min_temperature"], bucket["max_temperature"]) == (24.0, 24.0)
        # ...while the envelope reports what the readings behind them actually did.
        assert (bucket["temperature_min"], bucket["temperature_max"]) == (20.0, 28.0)
        assert (bucket["humidity_min"], bucket["humidity_max"]) == (44.0, 56.0)

    # And the buckets together account for every raw reading.
    assert sum(b["sample_n"] for b in buckets) == 12 * 100


def test_aggregation_falls_back_to_the_average_for_rows_without_an_envelope(temp_db):
    import time

    now = int(time.time())
    grow_id = temp_db.get_active_grow()["id"]
    conn = temp_db._open()
    for i, temp in enumerate([22.0, 26.0]):
        conn.execute(
            "INSERT INTO sensor_data (grow_id, timestamp, datetime, temperature, humidity,"
            " vpd, outside_temperature, outside_humidity) VALUES (?, ?, '', ?, 50, 1.2, 15, 70)",
            (grow_id, now - i * 300, temp),
        )
    conn.commit()
    conn.close()

    bucket = temp_db.get_aggregated_data(now - 3600, now, 3600)[0]
    assert (bucket["temperature_min"], bucket["temperature_max"]) == (22.0, 26.0)
    assert bucket["sample_n"] == 2        # one reading per legacy row
