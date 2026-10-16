"""
Schema creation: the one-time setup that runs before the first query.
"""

from __future__ import annotations

from datetime import datetime

from autocann.store import connection
from autocann.store.connection import _open
from autocann.store.migrations import (
    _backfill_stage_events,
    _migrate_nullable_outdoor_columns,
    _migrate_sample_detail_columns,
)
from autocann.store.seeds import _seed_builtin_profiles, _seed_feed_plan
from autocann.time import ARGENTINA_TZ

_schema_ready = False


def ensure_schema() -> None:
    """Create the schema once per process, on first use."""
    global _schema_ready
    if not _schema_ready:
        init_database()
        _schema_ready = True


def init_database() -> None:
    """
    Initialize the database and create tables if they don't exist.
    """
    # Create data directory if it doesn't exist
    connection.DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = _open()
    cursor = conn.cursor()

    # Create grows (cultivos) table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS grows (
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

    # Create sensor_data table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sensor_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grow_id INTEGER NOT NULL,
            timestamp INTEGER NOT NULL,
            datetime TEXT NOT NULL,
            temperature REAL NOT NULL,
            humidity REAL NOT NULL,
            vpd REAL NOT NULL,
            -- Nullable on purpose: when the outdoor sensor fails we record
            -- that we have no reading. The loop used to copy the indoor values
            -- instead, which wrote a fake outdoor curve into the history.
            outside_temperature REAL,
            outside_humidity REAL,
            leaf_temperature REAL,
            leaf_vpd REAL,
            target_humidity REAL,
            -- Interval summary: the row stands for sample_n readings, not one.
            temperature_min REAL,
            temperature_max REAL,
            humidity_min REAL,
            humidity_max REAL,
            sample_n INTEGER,
            -- Context, so the history can say what produced the reading.
            stage TEXT,
            indoor_source TEXT,
            control_action TEXT,
            quality TEXT,
            -- Uncorrected readings, kept so a recalibration does not make the
            -- existing history unreadable.
            temperature_raw REAL,
            humidity_raw REAL,
            outside_temperature_raw REAL,
            outside_humidity_raw REAL,
            FOREIGN KEY (grow_id) REFERENCES grows(id)
        )
    """
    )

    # Create index on timestamp for faster queries
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_timestamp
        ON sensor_data(timestamp)
    """
    )

    # Create index on grow_id for faster queries
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_grow_id
        ON sensor_data(grow_id)
    """
    )

    # Create control_events table for tracking humidity/ventilation changes
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS control_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            datetime TEXT NOT NULL,
            event_type TEXT NOT NULL,
            value TEXT NOT NULL
        )
    """
    )

    # Create index on timestamp for control events
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_control_timestamp
        ON control_events(timestamp)
    """
    )

    # Per-sensor correction. Two cheap sensors disagree by whole degrees, and
    # the offset has to live somewhere the loop can read on startup.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sensor_calibration (
            sensor_id TEXT PRIMARY KEY,
            temperature_offset REAL NOT NULL DEFAULT 0,
            humidity_offset REAL NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL,
            notes TEXT
        )
    """
    )

    # Stage history. `grows.stage` only holds the current value and is
    # overwritten on every change, so without this table there is no way to know
    # how long a grow has been in a stage.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS stage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grow_id INTEGER NOT NULL,
            stage TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            datetime TEXT NOT NULL,
            notes TEXT,
            FOREIGN KEY (grow_id) REFERENCES grows(id)
        )
    """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_stage_events_grow
        ON stage_events(grow_id, started_at)
    """
    )

    # Feed chart. The products a grower actually owns, and how much of each
    # goes in on a given week of a given stage. Kept editable in the database
    # rather than written into the code: brands and doses change between grows,
    # and the printed chart is only a starting point.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS feed_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT 'ml/L',
            position INTEGER NOT NULL DEFAULT 0
        )
    """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS feed_plan (
            product_id INTEGER NOT NULL,
            stage TEXT NOT NULL,
            week INTEGER NOT NULL,
            dose REAL NOT NULL,
            PRIMARY KEY (product_id, stage, week),
            FOREIGN KEY (product_id) REFERENCES feed_products(id) ON DELETE CASCADE
        )
    """
    )

    # What was actually poured, which is the point of the whole thing: the plan
    # says what should happen, this says what did.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS irrigations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grow_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            litres REAL NOT NULL,
            ph REAL,
            ec REAL,
            stage TEXT,
            week INTEGER,
            notes TEXT,
            FOREIGN KEY (grow_id) REFERENCES grows(id)
        )
    """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_irrigations_grow
        ON irrigations(grow_id, date DESC)
    """
    )
    # The name and unit are copied in, not just referenced: renaming a product
    # or dropping it from the plan must not rewrite what last month's log says
    # was poured.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS irrigation_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            irrigation_id INTEGER NOT NULL,
            product_id INTEGER,
            name TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT 'ml/L',
            dose REAL NOT NULL,
            on_plan INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (irrigation_id) REFERENCES irrigations(id) ON DELETE CASCADE
        )
    """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_irrigation_items
        ON irrigation_items(irrigation_id)
    """
    )

    # Settings the operator edits and the control loop obeys. One row per key,
    # value as JSON. In the database rather than in Redis because losing it has
    # to be loud: a quiet-hours window that silently vanished would show up as a
    # fan running at four in the morning.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT
        )
    """
    )

    # Crop profiles: what a crop needs, as data. Nothing reads these yet; the
    # control loop still uses the constants in vpd_math until a later step.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS crop_profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('builtin', 'custom')),
            created_at TEXT
        )
    """
    )
    # A stage has either a VPD band or an RH band (or both); which one is
    # enforced by `autocann.control.profiles.validate_profile_stages`, not here.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_stages (
            profile_id TEXT NOT NULL,
            key TEXT NOT NULL,
            name TEXT NOT NULL,
            position INTEGER NOT NULL,
            expected_days INTEGER,
            vpd_min REAL,
            vpd_max REAL,
            rh_min REAL,
            rh_max REAL,
            water_only INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (profile_id, key),
            FOREIGN KEY (profile_id) REFERENCES crop_profiles(id) ON DELETE CASCADE
        )
    """
    )

    conn.commit()

    _migrate_nullable_outdoor_columns(conn)
    _migrate_sample_detail_columns(conn)
    _backfill_stage_events(conn)
    _seed_feed_plan(conn)
    _seed_builtin_profiles(conn)

    # Create default grow if none exists
    cursor.execute("SELECT COUNT(*) FROM grows")
    count = cursor.fetchone()[0]
    if count == 0:
        current_time = datetime.now(ARGENTINA_TZ)
        stamp = current_time.strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            """
            INSERT INTO grows (name, stage, start_date, is_active, notes)
            VALUES (?, ?, ?, 1, ?)
        """,
            (
                "Cultivo #1",
                "early_veg",
                stamp,
                "Cultivo inicial creado automáticamente",
            ),
        )
        # Its opening stage event, written here rather than left to the
        # backfill: that already ran, a few lines above, when this grow did not
        # exist yet. Without this the default grow has no day counter until the
        # process restarts and the backfill picks it up.
        cursor.execute(
            """
            INSERT INTO stage_events (grow_id, stage, started_at, datetime, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (cursor.lastrowid, "early_veg", int(current_time.timestamp()), stamp,
             "Inicio del cultivo creado automáticamente"),
        )
        conn.commit()

    conn.close()
