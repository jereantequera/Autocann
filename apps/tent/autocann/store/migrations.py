"""
Schema migrations and backfills for databases created by older versions.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Optional

from autocann.time import ARGENTINA_TZ

logger = logging.getLogger(__name__)


def _migrate_nullable_outdoor_columns(conn: sqlite3.Connection) -> None:
    """
    Drop the NOT NULL constraint on the outdoor columns of an existing database.

    SQLite cannot relax NOT NULL with ALTER TABLE, so the table is rebuilt. Runs
    once: afterwards the columns already allow NULL and this is a no-op.

    Without it, every insert fails with "NOT NULL constraint failed" whenever the
    outdoor sensor has no reading — which silently stops the entire history.
    """
    cursor = conn.cursor()
    columns = cursor.execute("PRAGMA table_info(sensor_data)").fetchall()
    # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk)
    needs_migration = any(
        row[1] in ("outside_temperature", "outside_humidity") and row[3] == 1
        for row in columns
    )
    if not needs_migration:
        return

    print("🔧 Migrating sensor_data: making the outdoor columns nullable...")
    cursor.executescript(
        """
        PRAGMA foreign_keys=OFF;
        BEGIN;
        CREATE TABLE sensor_data_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grow_id INTEGER NOT NULL,
            timestamp INTEGER NOT NULL,
            datetime TEXT NOT NULL,
            temperature REAL NOT NULL,
            humidity REAL NOT NULL,
            vpd REAL NOT NULL,
            outside_temperature REAL,
            outside_humidity REAL,
            leaf_temperature REAL,
            leaf_vpd REAL,
            target_humidity REAL,
            FOREIGN KEY (grow_id) REFERENCES grows(id)
        );
        INSERT INTO sensor_data_new
            SELECT id, grow_id, timestamp, datetime, temperature, humidity, vpd,
                   outside_temperature, outside_humidity, leaf_temperature,
                   leaf_vpd, target_humidity
            FROM sensor_data;
        DROP TABLE sensor_data;
        ALTER TABLE sensor_data_new RENAME TO sensor_data;
        CREATE INDEX IF NOT EXISTS idx_timestamp ON sensor_data(timestamp);
        CREATE INDEX IF NOT EXISTS idx_grow_id ON sensor_data(grow_id);
        COMMIT;
        PRAGMA foreign_keys=ON;
        """
    )
    print("✅ Migration done.")


def _backfill_stage_events(conn: sqlite3.Connection) -> None:
    """
    Give every existing grow a first stage event, so the day counter has
    somewhere to start.

    The best available approximation is "the current stage began when the grow
    began" — the real transition dates were never recorded. From here on the
    history is exact.

    Idempotent: a grow that already has an event is skipped.
    """
    cursor = conn.cursor()
    rows = cursor.execute(
        """
        SELECT g.id, g.stage, g.start_date
        FROM grows g
        WHERE NOT EXISTS (SELECT 1 FROM stage_events s WHERE s.grow_id = g.id)
        """
    ).fetchall()
    if not rows:
        return

    inserted = 0
    for grow_id, stage, start_date in rows:
        started_at = _parse_local_datetime(start_date)
        if started_at is None:
            continue
        cursor.execute(
            """
            INSERT INTO stage_events (grow_id, stage, started_at, datetime, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (grow_id, stage, started_at, start_date,
             "Backfill: fecha estimada desde el inicio del cultivo"),
        )
        inserted += 1

    conn.commit()
    if inserted:
        print(f"🔧 stage_events: {inserted} cultivo(s) con etapa inicial estimada")


def _parse_local_datetime(value: Optional[str]) -> Optional[int]:
    """Epoch seconds for a stored 'YYYY-MM-DD HH:MM:SS' local timestamp."""
    if not value:
        return None
    try:
        naive = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    return int(ARGENTINA_TZ.localize(naive).timestamp())


#: Columns added in the "reliable data" phase, with their types. Adding a
#: nullable column is the one schema change SQLite does in place, so this needs
#: no table rebuild.
_SAMPLE_DETAIL_COLUMNS = (
    ("temperature_min", "REAL"),
    ("temperature_max", "REAL"),
    ("humidity_min", "REAL"),
    ("humidity_max", "REAL"),
    ("sample_n", "INTEGER"),
    ("stage", "TEXT"),
    ("indoor_source", "TEXT"),
    ("control_action", "TEXT"),
    ("quality", "TEXT"),
    ("temperature_raw", "REAL"),
    ("humidity_raw", "REAL"),
    ("outside_temperature_raw", "REAL"),
    ("outside_humidity_raw", "REAL"),
)


def _migrate_sample_detail_columns(conn: sqlite3.Connection) -> None:
    """
    Add the interval-summary, context and raw-reading columns.

    Existing rows keep NULL in all of them: they were written one instantaneous
    reading at a time and there is nothing to backfill them from. A NULL here
    honestly means "this sample predates the richer format".
    """
    cursor = conn.cursor()
    existing = {row[1] for row in cursor.execute("PRAGMA table_info(sensor_data)")}
    missing = [(name, kind) for name, kind in _SAMPLE_DETAIL_COLUMNS if name not in existing]
    if not missing:
        return

    for name, kind in missing:
        cursor.execute(f"ALTER TABLE sensor_data ADD COLUMN {name} {kind}")
    conn.commit()
    print(f"🔧 sensor_data: {len(missing)} columna(s) agregada(s) para el detalle de muestra")


#: Columns added to `grows` when tents and profiles arrived. Nullable, so SQLite
#: adds them in place; `_migrate_grow_links` fills the existing rows.
_GROW_LINK_COLUMNS = (
    ("tent_id", "INTEGER REFERENCES tents(id)"),
    ("profile_id", "TEXT REFERENCES crop_profiles(id)"),
)


def _migrate_grow_links(conn: sqlite3.Connection, tent_id: int, profile_id: str) -> None:
    """
    Add `grows.tent_id` and `grows.profile_id`, and link every grow without one.

    Every grow that existed before tents was the rig's only tent, running the
    built-in profile, so that is where the NULLs point. Idempotent: the columns
    are added once, and the UPDATEs only touch rows that are still NULL.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(grows)")}
    for name, kind in _GROW_LINK_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE grows ADD COLUMN {name} {kind}")
            logger.info("grows: added column %s", name)

    conn.execute("UPDATE grows SET tent_id = ? WHERE tent_id IS NULL", (tent_id,))
    conn.execute("UPDATE grows SET profile_id = ? WHERE profile_id IS NULL", (profile_id,))
    conn.commit()


def snapshot_profile_stages(conn: sqlite3.Connection, grow_id: int, profile_id: str) -> None:
    """
    Copy a profile's stages into `grow_stages` for one grow.

    One INSERT ... SELECT, so it is a copy by value: a later edit to
    `profile_stages` cannot reach the rows written here. The caller owns the
    transaction.
    """
    conn.execute(
        "INSERT INTO grow_stages (grow_id, key, name, position, expected_days, "
        "vpd_min, vpd_max, rh_min, rh_max, water_only) "
        "SELECT ?, key, name, position, expected_days, vpd_min, vpd_max, "
        "rh_min, rh_max, water_only FROM profile_stages WHERE profile_id = ?",
        (grow_id, profile_id),
    )


def _backfill_grow_stages(conn: sqlite3.Connection) -> None:
    """
    Give every grow that has no stage rows a snapshot of its profile's stages.

    Grows created before profiles existed get the built-in profile's stages as
    they are today. Idempotent: a grow that already has rows is skipped.
    """
    rows = conn.execute(
        """
        SELECT g.id, g.profile_id
        FROM grows g
        WHERE g.profile_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM grow_stages s WHERE s.grow_id = g.id)
        """
    ).fetchall()
    for grow_id, profile_id in rows:
        snapshot_profile_stages(conn, grow_id, profile_id)
    conn.commit()
    if rows:
        logger.info("grow_stages: snapshot taken for %d grow(s)", len(rows))


#: Column added to `grows` for the watering interval, in days. NULL means the
#: grow has no interval configured. Nullable, so SQLite adds it in place.
_IRRIGATION_INTERVAL_COLUMN = ("irrigation_interval_days", "INTEGER")


def _migrate_irrigation_interval(conn: sqlite3.Connection) -> None:
    """
    Add `grows.irrigation_interval_days`, the watering interval of each grow.

    Idempotent: the column is added once, and a database that has it is left
    alone. Existing grows start with NULL, which reads as "not configured".
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(grows)")}
    name, kind = _IRRIGATION_INTERVAL_COLUMN
    if name in existing:
        return
    conn.execute(f"ALTER TABLE grows ADD COLUMN {name} {kind}")
    conn.commit()
    logger.info("grows: added column %s", name)
