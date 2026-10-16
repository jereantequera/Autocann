from __future__ import annotations

import sqlite3

import pytest

import autocann.db as db
from autocann.store import connection as store_conn
from autocann.store import schema as store_schema


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the database layer at a throwaway file."""
    monkeypatch.setattr(store_conn, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(store_schema, "_schema_ready", False)
    db.ensure_schema()
    return db


def _query(sql, params=()):
    conn = db._open()
    try:
        return [tuple(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _execute(sql, params=()):
    conn = db._open()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _as_tuples(stages):
    """Compare stages by value, ignoring which table or grow they came from."""
    return [
        (s["key"], s["name"], s["position"], s["expected_days"], s["vpd_min"],
         s["vpd_max"], s["rh_min"], s["rh_max"], s["water_only"])
        for s in stages
    ]


def _profile_stages():
    return _as_tuples(db.get_profile(db.BUILTIN_PROFILE_ID)["stages"])


def _dump_store_tables():
    """Every row of the tables this change touches, for before/after comparisons."""
    return {
        table: _query(f"SELECT * FROM {table} ORDER BY 1, 2")
        for table in ("tents", "grows", "grow_stages", "crop_profiles", "profile_stages", "stage_events")
    }


# ---------------------------------------------------------------------------
# Migration of a database created before tents and grow stages
# ---------------------------------------------------------------------------


def _create_pre_52_database(path):
    """A database as the code before #52 left it: `grows` with its old columns and rows."""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE grows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            stage TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT,
            is_active INTEGER DEFAULT 1,
            notes TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO grows (name, stage, start_date, end_date, is_active, notes) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("Cultivo viejo", "flowering", "2026-01-01 08:00:00", "2026-03-01 08:00:00", 0, "cerrado"),
            ("Cultivo actual", "early_veg", "2026-03-01 08:00:00", None, 1, None),
        ],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """A pre-#52 database, opened by the store for the first time."""
    path = tmp_path / "old.db"
    _create_pre_52_database(path)
    monkeypatch.setattr(store_conn, "DB_PATH", path)
    monkeypatch.setattr(store_schema, "_schema_ready", False)
    db.ensure_schema()
    return db


def test_old_grows_are_linked_to_tent_one_and_the_builtin_profile(old_db):
    grows = old_db.get_all_grows()
    assert {g["name"] for g in grows} == {"Cultivo viejo", "Cultivo actual"}
    for grow in grows:
        assert grow["tent_id"] == db.BUILTIN_TENT_ID == 1
        assert grow["profile_id"] == db.BUILTIN_PROFILE_ID


def test_old_grows_get_a_snapshot_of_the_builtin_profile_stages(old_db):
    grow_ids = [row[0] for row in _query("SELECT id FROM grows ORDER BY id")]
    assert len(grow_ids) == 2
    for grow_id in grow_ids:
        assert _as_tuples(old_db.get_grow_stages(grow_id)) == _profile_stages()


def test_old_grow_keeps_its_stage_and_its_active_flag(old_db):
    rows = _query("SELECT name, stage, is_active, end_date FROM grows ORDER BY id")
    assert rows == [
        ("Cultivo viejo", "flowering", 0, "2026-03-01 08:00:00"),
        ("Cultivo actual", "early_veg", 1, None),
    ]
    assert old_db.get_active_grow()["name"] == "Cultivo actual"


def test_running_the_schema_setup_again_on_an_old_database_changes_nothing(old_db):
    before = _dump_store_tables()

    old_db.init_database()
    assert _dump_store_tables() == before

    store_schema._schema_ready = False
    old_db.ensure_schema()
    assert _dump_store_tables() == before


# ---------------------------------------------------------------------------
# Tents
# ---------------------------------------------------------------------------


def test_tent_one_is_seeded_as_carpa_principal(temp_db):
    tent = temp_db.get_tent(1)
    assert tent["id"] == 1
    assert tent["name"] == "Carpa principal"
    assert tent["created_at"]
    assert temp_db.get_tent(99) is None


def test_renaming_tent_one_survives_running_the_setup_again(temp_db):
    assert temp_db.rename_tent(1, "  Carpa norte  ") is True
    temp_db.init_database()
    assert temp_db.get_tent(1)["name"] == "Carpa norte"
    assert _query("SELECT COUNT(*) FROM tents") == [(1,)]


def test_rename_tent_refuses_a_blank_name_or_an_unknown_tent(temp_db):
    assert temp_db.rename_tent(1, "   ") is False
    assert temp_db.rename_tent(99, "Otra") is False
    assert temp_db.get_tent(1)["name"] == "Carpa principal"


# ---------------------------------------------------------------------------
# Grows: profile snapshot and tent scope
# ---------------------------------------------------------------------------


def test_a_new_grow_has_its_own_snapshot_of_the_profile_stages(temp_db):
    grow_id = temp_db.create_grow("Nuevo", "early_veg")

    assert _as_tuples(temp_db.get_grow_stages(grow_id)) == _profile_stages()
    grow = temp_db.get_active_grow()
    assert grow["id"] == grow_id
    assert grow["tent_id"] == 1
    assert grow["profile_id"] == db.BUILTIN_PROFILE_ID


def test_grow_stages_are_ordered_by_position(temp_db):
    grow_id = temp_db.create_grow("Orden", "early_veg")
    positions = [stage["position"] for stage in temp_db.get_grow_stages(grow_id)]
    assert positions == sorted(positions)
    assert temp_db.get_grow_stages(999) == []


def test_editing_the_profile_does_not_change_an_existing_grow(temp_db):
    old_grow = temp_db.create_grow("Viejo", "early_veg")
    before = temp_db.get_grow_stages(old_grow)

    _execute(
        "UPDATE profile_stages SET vpd_max = 2.5, expected_days = 99 "
        "WHERE profile_id = ? AND key = 'early_veg'",
        (db.BUILTIN_PROFILE_ID,),
    )
    _execute(
        "UPDATE profile_stages SET name = 'Renombrada' WHERE profile_id = ? AND key = 'flowering'",
        (db.BUILTIN_PROFILE_ID,),
    )

    assert temp_db.get_grow_stages(old_grow) == before

    new_grow = temp_db.create_grow("Nuevo", "early_veg")
    new_stages = {stage["key"]: stage for stage in temp_db.get_grow_stages(new_grow)}
    assert new_stages["early_veg"]["vpd_max"] == 2.5
    assert new_stages["early_veg"]["expected_days"] == 99
    assert new_stages["flowering"]["name"] == "Renombrada"


def test_a_stage_outside_the_profile_is_rejected_and_nothing_is_written(temp_db):
    active = temp_db.create_grow("Activo", "early_veg")
    grows_before = _query("SELECT COUNT(*) FROM grows")
    stages_before = _query("SELECT COUNT(*) FROM grow_stages")

    assert temp_db.create_grow("Malo", "no_such_stage") is None

    assert _query("SELECT COUNT(*) FROM grows") == grows_before
    assert _query("SELECT COUNT(*) FROM grow_stages") == stages_before
    assert temp_db.get_active_grow()["id"] == active


def test_a_missing_profile_is_rejected(temp_db):
    assert temp_db.create_grow("Sin perfil", "early_veg", profile_id="does-not-exist") is None


def test_a_missing_tent_is_rejected(temp_db):
    assert temp_db.create_grow("Sin carpa", "early_veg", tent_id=42) is None


def test_a_grow_in_tent_two_does_not_deactivate_tent_one(temp_db):
    _execute("INSERT INTO tents (id, name, created_at) VALUES (2, 'Carpa 2', '2026-01-01 00:00:00')")

    tent_one = temp_db.create_grow("Carpa 1", "early_veg")
    tent_two = temp_db.create_grow("Carpa 2", "early_veg", tent_id=2)

    active = {row[0] for row in _query("SELECT id FROM grows WHERE is_active = 1")}
    assert active == {tent_one, tent_two}
    assert _query("SELECT tent_id FROM grows WHERE id = ?", (tent_two,)) == [(2,)]


def test_a_new_grow_in_a_tent_deactivates_only_that_tents_grow(temp_db):
    _execute("INSERT INTO tents (id, name, created_at) VALUES (2, 'Carpa 2', '2026-01-01 00:00:00')")
    tent_one = temp_db.create_grow("Carpa 1", "early_veg")
    tent_two = temp_db.create_grow("Carpa 2", "early_veg", tent_id=2)

    newer_tent_one = temp_db.create_grow("Carpa 1 bis", "late_veg")

    active = {row[0] for row in _query("SELECT id FROM grows WHERE is_active = 1")}
    assert active == {tent_two, newer_tent_one}
    assert tent_one not in active


def test_the_default_grow_created_with_a_fresh_database_has_its_snapshot(temp_db):
    grows = _query("SELECT id, tent_id, profile_id FROM grows")
    assert len(grows) == 1
    grow_id, tent_id, profile_id = grows[0]
    assert (tent_id, profile_id) == (1, db.BUILTIN_PROFILE_ID)
    assert _as_tuples(temp_db.get_grow_stages(grow_id)) == _profile_stages()
