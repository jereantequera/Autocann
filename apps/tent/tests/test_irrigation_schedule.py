"""
The irrigation schedule below the API: the pure due-date rules, the interval
and last-watering store functions, and the migration that adds the column.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from autocann.control.irrigation import next_due
from autocann.store.irrigation_schedule import (
    get_irrigation_interval,
    last_irrigation_date,
    set_irrigation_interval,
)
from autocann.store.migrations import _migrate_irrigation_interval

TODAY = date(2026, 10, 17)


def _ago(days: int) -> date:
    return TODAY - timedelta(days=days)


# --------------------------------------------------------------------------
# next_due: the pure rules
# --------------------------------------------------------------------------

def test_due_today_when_the_interval_ends_today():
    result = next_due(_ago(2), 2, TODAY)
    assert result == {"due_date": "2026-10-17", "days_until": 0, "overdue_days": 0, "state": "today"}


def test_upcoming_counts_the_days_left():
    result = next_due(_ago(1), 3, TODAY)
    assert result == {"due_date": "2026-10-19", "days_until": 2, "overdue_days": 0, "state": "upcoming"}


def test_watered_today_is_due_again_tomorrow_with_a_one_day_interval():
    result = next_due(TODAY, 1, TODAY)
    assert result["state"] == "upcoming"
    assert result["days_until"] == 1


def test_overdue_by_one_day_is_counted_from_the_last_watering():
    # Watered 17 Oct minus 3 days, every 2 days: due 16 Oct, not watered since.
    result = next_due(_ago(3), 2, TODAY)
    assert result == {"due_date": "2026-10-16", "days_until": -1, "overdue_days": 1, "state": "overdue"}


def test_overdue_by_three_days_still_rolls_from_the_last_watering():
    # Watered 12 Oct, every 2 days: due 14 Oct, three days ago.
    result = next_due(_ago(5), 2, TODAY)
    assert result == {"due_date": "2026-10-14", "days_until": -3, "overdue_days": 3, "state": "overdue"}


def test_no_history_is_due_today():
    result = next_due(None, 4, TODAY)
    assert result == {"due_date": "2026-10-17", "days_until": 0, "overdue_days": 0, "state": "no_history"}


def test_changing_the_interval_moves_the_due_date_from_the_last_watering():
    assert next_due(_ago(2), 2, TODAY)["state"] == "today"
    moved = next_due(_ago(2), 5, TODAY)
    assert moved["state"] == "upcoming"
    assert moved["days_until"] == 3
    assert moved["due_date"] == "2026-10-20"


@pytest.mark.parametrize("interval", [0, -1, 31])
def test_an_interval_outside_one_to_thirty_is_refused(interval):
    with pytest.raises(ValueError):
        next_due(_ago(1), interval, TODAY)


# --------------------------------------------------------------------------
# Store: interval and last watering
# --------------------------------------------------------------------------

def test_a_new_grow_has_no_interval_until_one_is_set(temp_db):
    grow_id = temp_db.get_active_grow()["id"]
    assert get_irrigation_interval(grow_id) is None


def test_the_interval_is_set_read_and_cleared(temp_db):
    grow_id = temp_db.get_active_grow()["id"]
    assert set_irrigation_interval(grow_id, 3) is True
    assert get_irrigation_interval(grow_id) == 3
    assert set_irrigation_interval(grow_id, None) is True
    assert get_irrigation_interval(grow_id) is None


def test_setting_an_interval_for_a_missing_grow_reports_it(temp_db):
    assert set_irrigation_interval(9999, 2) is False


def test_the_last_watering_is_the_most_recent_date(temp_db):
    grow_id = temp_db.get_active_grow()["id"]
    assert last_irrigation_date(grow_id) is None
    temp_db.record_irrigation(grow_id, 5, [], date="2026-10-01")
    temp_db.record_irrigation(grow_id, 5, [], date="2026-10-09")
    temp_db.record_irrigation(grow_id, 5, [], date="2026-10-04")
    assert last_irrigation_date(grow_id) == "2026-10-09"


# --------------------------------------------------------------------------
# Migration
# --------------------------------------------------------------------------

def _columns(conn, table):
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def test_the_migration_adds_the_column_to_an_old_grows_table_once(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE grows (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
        "stage TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT, is_active INTEGER "
        "DEFAULT 1, notes TEXT)"
    )
    conn.execute("INSERT INTO grows (name, stage, start_date) VALUES ('Old', 'early_veg', '2026-01-01')")
    conn.commit()

    _migrate_irrigation_interval(conn)
    _migrate_irrigation_interval(conn)  # second run is a no-op

    assert _columns(conn, "grows").count("irrigation_interval_days") == 1
    value = conn.execute("SELECT irrigation_interval_days FROM grows").fetchone()[0]
    assert value is None
    conn.close()


def test_a_fresh_database_has_the_column_from_the_schema(temp_db):
    from autocann.store.connection import _open

    conn = _open()
    try:
        assert "irrigation_interval_days" in _columns(conn, "grows")
    finally:
        conn.close()
