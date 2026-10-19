"""
One active grow per tent: switching, reading, the migration that enforces it,
and the unique index that keeps it enforced.
"""

from __future__ import annotations

import sqlite3

import pytest

import autocann.db as db
from autocann.store import schema as store_schema
from autocann.store.seeds import BUILTIN_PROFILE_ID


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


def _add_tent(tent_id: int, name: str) -> None:
    _execute(
        "INSERT INTO tents (id, name, created_at) VALUES (?, ?, '2026-01-01 00:00:00')",
        (tent_id, name),
    )


def _active_ids(tent_id: int):
    return [row[0] for row in _query(
        "SELECT id FROM grows WHERE is_active = 1 AND tent_id = ? ORDER BY id", (tent_id,))]


def test_activating_a_grow_in_tent_2_leaves_tent_1_alone(temp_db):
    _add_tent(2, "Carpa 2")
    tent_1_grow = db.get_active_grow()["id"]  # the default grow, in tent 1

    tent_2_first = db.create_grow("Tent 2 first", tent_id=2)
    tent_2_second = db.create_grow("Tent 2 second", tent_id=2)
    assert _active_ids(2) == [tent_2_second]
    assert _active_ids(1) == [tent_1_grow]

    assert db.set_active_grow(tent_2_first) is True

    assert _active_ids(2) == [tent_2_first]
    assert _active_ids(1) == [tent_1_grow]
    assert db.get_active_grow(2)["id"] == tent_2_first
    assert db.get_active_grow()["id"] == tent_1_grow
    assert db.get_active_grow(1)["id"] == tent_1_grow


def test_set_active_grow_on_a_missing_grow_writes_nothing(temp_db):
    before = db.get_active_grow()["id"]

    assert db.set_active_grow(9999) is False

    assert db.get_active_grow()["id"] == before


def test_migration_keeps_only_the_newest_active_grow_per_tent(temp_db):
    # A database from before the rule: the index is gone and tent 1 has three
    # active grows.
    _execute("DROP INDEX idx_one_active_grow_per_tent")
    for name in ("older", "newer"):
        _execute(
            "INSERT INTO grows (name, stage, start_date, is_active, notes, tent_id, profile_id) "
            "VALUES (?, 'early_veg', '2026-01-01 00:00:00', 1, '', 1, ?)",
            (name, BUILTIN_PROFILE_ID),
        )
    default_id = _query("SELECT MIN(id) FROM grows")[0][0]
    newest_id = _query("SELECT MAX(id) FROM grows")[0][0]
    assert len(_active_ids(1)) == 3

    store_schema.init_database()

    assert _active_ids(1) == [newest_id]
    assert _query("SELECT is_active FROM grows WHERE id = ?", (default_id,)) == [(0,)]
    index = _query(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'idx_one_active_grow_per_tent'")
    assert len(index) == 1
    assert "UNIQUE" in index[0][0].upper()
    assert "is_active = 1" in index[0][0]


def test_running_init_twice_changes_nothing(temp_db):
    _add_tent(2, "Carpa 2")
    db.create_grow("Tent 2 grow", tent_id=2)

    def snapshot():
        return (
            _query("SELECT id, is_active, tent_id, profile_id FROM grows ORDER BY id"),
            _query("SELECT name, sql FROM sqlite_master WHERE type = 'index' ORDER BY name"),
        )

    before = snapshot()
    store_schema.init_database()
    store_schema.init_database()

    assert snapshot() == before


def test_unique_index_rejects_a_second_active_grow_in_the_same_tent(temp_db):
    conn = db._open()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO grows (name, stage, start_date, is_active, notes, tent_id, profile_id) "
                "VALUES ('second', 'early_veg', '2026-01-01 00:00:00', 1, '', 1, ?)",
                (BUILTIN_PROFILE_ID,),
            )
    finally:
        conn.close()
