"""
Photoperiod per grow: the migration, the store and the endpoints.
"""

from __future__ import annotations

import sqlite3

import pytest

from autocann.store import connection as store_conn
from autocann.store import photoperiod as store
from autocann.store.migrations import _migrate_grow_photoperiod


def _columns(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(grows)")}
    finally:
        conn.close()


def test_the_migration_adds_both_columns_to_an_old_grows_table(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE grows (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    conn.execute("INSERT INTO grows (name) VALUES ('Vieja')")
    conn.commit()

    _migrate_grow_photoperiod(conn)

    assert {"lights_on_hour", "lights_off_hour"} <= {row[1] for row in conn.execute("PRAGMA table_info(grows)")}
    # Existing rows are kept, and they read as "not configured".
    assert conn.execute("SELECT name, lights_on_hour, lights_off_hour FROM grows").fetchall() == [
        ("Vieja", None, None)
    ]
    conn.close()


def test_the_migration_is_idempotent(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE grows (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")

    _migrate_grow_photoperiod(conn)
    _migrate_grow_photoperiod(conn)  # second run must not try to add them again

    assert {"lights_on_hour", "lights_off_hour"} <= {row[1] for row in conn.execute("PRAGMA table_info(grows)")}
    conn.close()


def test_initialising_the_schema_twice_is_safe(temp_db):
    temp_db.init_database()
    temp_db.init_database()
    assert {"lights_on_hour", "lights_off_hour"} <= _columns(store_conn.DB_PATH)


def test_a_grow_without_hours_reads_the_defaults(temp_db):
    grow = temp_db.create_grow("Prueba", "early_veg")

    found = temp_db.get_photoperiod(grow)

    assert found == {"on": temp_db.DEFAULT_LIGHTS_ON_HOUR, "off": temp_db.DEFAULT_LIGHTS_OFF_HOUR,
                     "configured": False}


def test_the_defaults_are_six_on_and_midnight_off():
    assert store.DEFAULT_LIGHTS_ON_HOUR == 6
    assert store.DEFAULT_LIGHTS_OFF_HOUR == 0


def test_a_saved_photoperiod_is_read_back(temp_db):
    grow = temp_db.create_grow("Prueba", "early_veg")

    saved = temp_db.set_photoperiod(grow, 18, 12)

    assert saved == {"on": 18, "off": 12, "configured": True}
    assert temp_db.get_photoperiod(grow) == saved


def test_the_active_grow_carries_its_photoperiod(temp_db):
    grow = temp_db.create_grow("Prueba", "early_veg")
    temp_db.set_photoperiod(grow, 5, 23)

    active = temp_db.get_active_grow()

    assert (active["lights_on_hour"], active["lights_off_hour"]) == (5, 23)


def test_an_unknown_grow_has_no_photoperiod(temp_db):
    assert temp_db.get_photoperiod(9999) is None
    assert temp_db.set_photoperiod(9999, 6, 0) is None


@pytest.mark.parametrize("on, off", [(6, 6), (-1, 5), (5, 24), (None, 3), (True, 3), ("6", 3), (6.0, 3)])
def test_invalid_hours_are_refused(on, off):
    with pytest.raises(ValueError):
        store.validate_photoperiod(on, off)


def test_an_overnight_window_is_valid():
    assert store.validate_photoperiod(18, 12) == (18, 12)
    assert store.validate_photoperiod(6, 0) == (6, 0)


def test_lights_hours_of_a_window():
    assert store.lights_hours(6, 0) == 18
    assert store.lights_hours(18, 12) == 18
    assert store.lights_hours(6, 12) == 6


# ===============================
# Endpoints
# ===============================

def test_the_photoperiod_endpoint_reads_and_writes(client, temp_db):
    grow = temp_db.create_grow("Prueba", "early_veg")

    assert client.get(f"/api/grows/{grow}/photoperiod").get_json()["configured"] is False

    response = client.put(f"/api/grows/{grow}/photoperiod", json={"on": 18, "off": 12})
    assert response.status_code == 200
    assert response.get_json() == {"on": 18, "off": 12, "configured": True}
    assert client.get(f"/api/grows/{grow}/photoperiod").get_json() == {"on": 18, "off": 12, "configured": True}


@pytest.mark.parametrize("body", [
    {"on": 6, "off": 6},
    {"on": 24, "off": 0},
    {"on": -1, "off": 0},
    {"on": "6", "off": 0},
    {"on": True, "off": 0},
    {"on": 6},
    {"off": 0},
    [6, 0],
    None,
])
def test_the_photoperiod_endpoint_refuses_bad_bodies(client, temp_db, body):
    grow = temp_db.create_grow("Prueba", "early_veg")

    response = client.put(f"/api/grows/{grow}/photoperiod", json=body)

    assert response.status_code == 400
    assert "error" in response.get_json()
    assert temp_db.get_photoperiod(grow)["configured"] is False


def test_the_photoperiod_endpoint_is_404_for_an_unknown_grow(client):
    assert client.get("/api/grows/9999/photoperiod").status_code == 404
    assert client.put("/api/grows/9999/photoperiod", json={"on": 6, "off": 0}).status_code == 404
