#!/usr/bin/env python

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from autocann.control.anomalies import find_anomalies
from autocann.control.feeding import WATER_ONLY_STAGES
from autocann.control.plausible import AND_VALID_ROW
from autocann.control.vpd_math import (
    HUMIDITY_RANGES,
    STAGE_EXPECTED_DAYS,
    STAGE_NAMES,
    VPD_RANGES,
)
from autocann.paths import DB_PATH
from autocann.time import ARGENTINA_TZ

#: Wait this long for a competing writer before raising "database is locked".
#: The control loop and the web app both write, so they do collide.
BUSY_TIMEOUT_SECONDS = 10.0

#: Ask for every grow at once, on purpose.
#:
#: `grow_id=None` means "the grow the dashboard is about", so without this there
#: was no way to spell "all of them" — and the only query that ever returned
#: every grow was the one that did it by accident, when no grow happened to be
#: active. Crossing ten months of empty tent with a real crop should take saying
#: so.
ALL_GROWS = "all"

#: Returned by `_resolve_grow_filter` when the caller asked for the active grow
#: and there is not one. Distinct from `None`, which there means "no filter".
_NO_GROW = object()

_schema_ready = False


def _resolve_grow_filter(grow_id):
    """
    Which grow a query should filter on.

    Three answers, and the third is the point:

    - an **integer** — that grow;
    - **`ALL_GROWS`** — `None`, i.e. no `WHERE grow_id`;
    - **`None`** (the default) — the active grow, or `_NO_GROW` when there is
      none.

    That last case used to fall through to no filter at all, which is how the
    history silently became "every grow ever recorded" in exactly the window
    that matters: between ending one grow and starting the next. An empty
    answer is the honest one there — there is no active grow to report on — and
    it leaves "all of them" as something somebody chose rather than something
    that happened.
    """
    if isinstance(grow_id, str) and grow_id.strip().lower() == ALL_GROWS:
        return None
    if grow_id is not None:
        return int(grow_id)
    active_grow = get_active_grow()
    if active_grow:
        return int(active_grow["id"])
    return _NO_GROW


def _open(row_factory: bool = False) -> sqlite3.Connection:
    """
    Open a connection with the settings this workload needs.

    WAL lets the dashboard read while the control loop writes instead of both
    blocking each other, and busy_timeout replaces an immediate "database is
    locked" error with a short wait.
    """
    conn = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_SECONDS)
    if row_factory:
        conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_SECONDS * 1000)}")
    return conn


def ensure_schema() -> None:
    """Create the schema once per process, on first use."""
    global _schema_ready
    if not _schema_ready:
        init_database()
        _schema_ready = True


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


#: The Advanced Nutrients pH Perfect chart, mapped onto this project's stages.
#:
#: The printed chart runs four weeks of "crecimiento" and eight of floración,
#: while a grow here passes through early_veg (3 weeks) and late_veg (4), so the
#: base ramp 1-2-3-4 mL/L lands on the three early weeks and then holds at 4 for
#: the whole of late veg. The last week of flowering is deliberately empty: that
#: is the flush.
#:
#: Only a starting point. It is written once, on an empty database, and is
#: editable from the dashboard afterwards.
_FEED_PLAN_SEED: Tuple[Tuple[str, str, Dict[str, Dict[int, float]]], ...] = (
    ("pH Perfect Grow", "ml/L", {
        "early_veg": {1: 1, 2: 2, 3: 3},
        "late_veg": {1: 4, 2: 4, 3: 4, 4: 4},
        "flowering": {1: 4, 2: 4, 3: 4, 4: 4, 5: 4, 6: 4, 7: 4},
    }),
    ("pH Perfect Micro", "ml/L", {
        "early_veg": {1: 1, 2: 2, 3: 3},
        "late_veg": {1: 4, 2: 4, 3: 4, 4: 4},
        "flowering": {1: 4, 2: 4, 3: 4, 4: 4, 5: 4, 6: 4, 7: 4},
    }),
    ("pH Perfect Bloom", "ml/L", {
        "early_veg": {1: 1, 2: 2, 3: 3},
        "late_veg": {1: 4, 2: 4, 3: 4, 4: 4},
        "flowering": {1: 4, 2: 4, 3: 4, 4: 4, 5: 4, 6: 4, 7: 4},
    }),
    ("B-52", "ml/L", {
        "early_veg": {2: 2, 3: 2},
        "late_veg": {1: 2, 2: 2, 3: 2, 4: 2},
        "flowering": {1: 2, 2: 2, 3: 2, 4: 2},
    }),
    ("Bud Candy", "ml/L", {
        "early_veg": {2: 2, 3: 2},
        "late_veg": {1: 2, 2: 2, 3: 2, 4: 2},
        "flowering": {1: 2, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2, 7: 2},
    }),
    ("Big Bud", "ml/L", {
        "flowering": {2: 2, 3: 2, 4: 2},
    }),
    ("Overdrive", "ml/L", {
        "flowering": {6: 2, 7: 2},
    }),
    ("Rhino Skin", "ml/L", {
        "early_veg": {3: 2},
        "late_veg": {1: 2, 2: 2, 3: 2, 4: 2},
        "flowering": {1: 2, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2},
    }),
    ("Extracto de algas", "ml/L", {
        "flowering": {2: 2, 3: 2, 4: 2, 5: 2, 6: 2},
    }),
    ("Micorrizas", "g/L", {
        "early_veg": {1: 1},
    }),
)


def _seed_feed_plan(conn: sqlite3.Connection) -> None:
    """
    Write the default feed chart, once, into an empty products table.

    Skipped as soon as there is a single product, so a grower who deletes a
    product they do not own never sees it reappear.

    The count and the inserts share one BEGIN IMMEDIATE transaction because the
    web app and the control loop both call ensure_schema() on startup and
    systemd starts them together. Counting outside a write lock lets both read
    an empty table on a fresh install and seed it, leaving the chart with every
    product twice. The loser of the lock waits out busy_timeout, then sees the
    rows and returns.
    """
    conn.commit()  # an earlier migration may still hold an implicit transaction
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("SELECT COUNT(*) FROM feed_products").fetchone()[0]:
            conn.rollback()
            return

        for position, (name, unit, doses) in enumerate(_FEED_PLAN_SEED):
            cursor = conn.execute(
                "INSERT INTO feed_products (name, unit, position) VALUES (?, ?, ?)",
                (name, unit, position),
            )
            product_id = cursor.lastrowid
            conn.executemany(
                "INSERT INTO feed_plan (product_id, stage, week, dose) VALUES (?, ?, ?, ?)",
                [
                    (product_id, stage, week, dose)
                    for stage, weeks in doses.items()
                    for week, dose in weeks.items()
                ],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


#: The built-in profile every install gets. Its stages are built from the
#: constants in `vpd_math`, so the two cannot drift apart.
BUILTIN_PROFILE_ID = "cannabis-photoperiod"
BUILTIN_PROFILE_NAME = "Cannabis fotoperiódico"


def _builtin_profile_stage_rows() -> List[Tuple]:
    """The built-in profile's stages as `profile_stages` rows, in cycle order."""
    rows = []
    for position, (key, (vpd_min, vpd_max)) in enumerate(VPD_RANGES.items()):
        humidity = HUMIDITY_RANGES.get(key)
        rh_min, rh_max = humidity if humidity else (None, None)
        rows.append((
            key,
            STAGE_NAMES[key],
            position,
            STAGE_EXPECTED_DAYS[key],
            vpd_min,
            vpd_max,
            rh_min,
            rh_max,
            int(key in WATER_ONLY_STAGES),
        ))
    return rows


def _seed_builtin_profiles(conn: sqlite3.Connection) -> None:
    """
    Write the built-in profile, once. Never overwrites it.

    Skipped as soon as a profile with its id exists, so a person who edits its
    stages later keeps their changes. The check and the inserts share one
    BEGIN IMMEDIATE transaction for the same reason `_seed_feed_plan` does: the
    web app and the control loop both start the schema, and the loser of the
    lock must see the winner's rows and stop.
    """
    conn.commit()  # an earlier migration may still hold an implicit transaction
    conn.execute("BEGIN IMMEDIATE")
    try:
        exists = conn.execute(
            "SELECT 1 FROM crop_profiles WHERE id = ?", (BUILTIN_PROFILE_ID,)
        ).fetchone()
        if exists:
            conn.rollback()
            return

        conn.execute(
            "INSERT INTO crop_profiles (id, name, kind, created_at) VALUES (?, ?, 'builtin', ?)",
            (
                BUILTIN_PROFILE_ID,
                BUILTIN_PROFILE_NAME,
                datetime.now(ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.executemany(
            "INSERT INTO profile_stages (profile_id, key, name, position, expected_days, "
            "vpd_min, vpd_max, rh_min, rh_max, water_only) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(BUILTIN_PROFILE_ID, *row) for row in _builtin_profile_stage_rows()],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_profiles() -> List[Dict]:
    """Every crop profile, built-in first then by name. Each is `id`, `name`, `kind`."""
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        try:
            rows = conn.execute(
                "SELECT id, name, kind FROM crop_profiles ORDER BY kind = 'builtin' DESC, name ASC"
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]
    except sqlite3.Error as e:
        print(f"Error getting crop profiles: {e}")
        return []


def get_profile(profile_id: str) -> Optional[Dict]:
    """
    One profile with its `stages` in position order, or None when it does not exist.

    Each stage is a dict with every `profile_stages` column; `water_only` is a bool.
    """
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        try:
            profile = conn.execute(
                "SELECT id, name, kind FROM crop_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            if profile is None:
                return None
            stages = conn.execute(
                "SELECT profile_id, key, name, position, expected_days, vpd_min, vpd_max, "
                "rh_min, rh_max, water_only FROM profile_stages "
                "WHERE profile_id = ? ORDER BY position ASC",
                (profile_id,),
            ).fetchall()
        finally:
            conn.close()
        return {
            **dict(profile),
            "stages": [{**dict(row), "water_only": bool(row["water_only"])} for row in stages],
        }
    except sqlite3.Error as e:
        print(f"Error getting crop profile '{profile_id}': {e}")
        return None


def init_database() -> None:
    """
    Initialize the database and create tables if they don't exist.
    """
    # Create data directory if it doesn't exist
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

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


def get_active_grow() -> Optional[Dict]:
    """
    Get the currently active grow.

    Returns:
    - Dictionary with grow information or None
    """
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT * FROM grows
            WHERE is_active = 1
            ORDER BY id DESC
            LIMIT 1
        """
        )

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        grow = dict(row)
        grow.update(_grow_day_counters(grow))
        return grow
    except Exception as e:
        print(f"Error getting active grow: {e}")
        return None


def _grow_day_counters(grow: Dict, now: Optional[int] = None) -> Dict:
    """
    Day-in-stage and day-of-grow for a grow row.

    Kept separate so a failure here degrades the counters to None instead of
    taking down get_active_grow(), which the control loop depends on.
    """
    from autocann.control.stages import build_timeline, current_period, day_number, estimated_end_timestamp

    empty = {
        "stage_started_at": None, "day_in_stage": None, "day_of_grow": None,
        "stage_expected_days": None, "stage_progress": None, "estimated_end": None,
    }
    try:
        if now is None:
            now = int(datetime.now(ARGENTINA_TZ).timestamp())

        periods = build_timeline(get_stage_events(int(grow["id"])), now=now)
        current = current_period(periods)
        started = _parse_local_datetime(grow.get("start_date"))

        return {
            "stage_started_at": current.started_at if current else None,
            "day_in_stage": current.days if current else None,
            "day_of_grow": day_number(started, now) if started else None,
            "stage_expected_days": current.expected_days if current else None,
            "stage_progress": current.progress if current else None,
            "estimated_end": estimated_end_timestamp(periods, now),
        }
    except Exception as e:
        print(f"Error computing grow day counters: {e}")
        return empty


def create_grow(name: str, stage: str = "early_veg", notes: str = "") -> Optional[int]:
    """
    Create a new grow and set it as active.
    """
    try:
        current_time = datetime.now(ARGENTINA_TZ)

        ensure_schema()
        conn = _open()
        cursor = conn.cursor()

        # Deactivate all other grows
        cursor.execute("UPDATE grows SET is_active = 0")

        # Create new grow
        cursor.execute(
            """
            INSERT INTO grows (name, stage, start_date, is_active, notes)
            VALUES (?, ?, ?, 1, ?)
        """,
            (name, stage, current_time.strftime("%Y-%m-%d %H:%M:%S"), notes),
        )

        grow_id = cursor.lastrowid
        conn.commit()
        conn.close()

        if grow_id is not None:
            record_stage_event(int(grow_id), stage,
                               started_at=int(current_time.timestamp()),
                               notes="Inicio del cultivo")
        return int(grow_id) if grow_id is not None else None
    except Exception as e:
        print(f"Error creating grow: {e}")
        return None


def end_grow(grow_id: int) -> bool:
    """
    End a grow by setting its end date and deactivating it.
    """
    try:
        current_time = datetime.now(ARGENTINA_TZ)

        ensure_schema()
        conn = _open()
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE grows
            SET end_date = ?, is_active = 0
            WHERE id = ?
        """,
            (current_time.strftime("%Y-%m-%d %H:%M:%S"), grow_id),
        )

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error ending grow: {e}")
        return False


def set_active_grow(grow_id: int) -> bool:
    """
    Set a grow as active (and deactivate all others).
    """
    try:
        ensure_schema()
        conn = _open()
        cursor = conn.cursor()

        # Deactivate all grows
        cursor.execute("UPDATE grows SET is_active = 0")

        # Activate selected grow
        cursor.execute("UPDATE grows SET is_active = 1 WHERE id = ?", (grow_id,))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error setting active grow: {e}")
        return False


def update_grow_stage(grow_id: int, stage: str, notes: Optional[str] = None) -> bool:
    """
    Move a grow to a stage and record the transition.

    Re-setting the stage it is already in is a no-op for the history: it would
    otherwise restart the day counter on an accidental double click.
    """
    try:
        ensure_schema()
        conn = _open()
        cursor = conn.cursor()
        row = cursor.execute("SELECT stage FROM grows WHERE id = ?", (grow_id,)).fetchone()
        if row is None:
            conn.close()
            print(f"Error updating grow stage: grow {grow_id} not found")
            return False
        previous = row[0]

        cursor.execute("UPDATE grows SET stage = ? WHERE id = ?", (stage, grow_id))
        conn.commit()
        conn.close()

        if previous != stage:
            record_stage_event(grow_id, stage, notes=notes)
        return True
    except Exception as e:
        print(f"Error updating grow stage: {e}")
        return False


def get_all_grows() -> List[Dict]:
    """
    Get all grows.
    """
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT g.*,
                   COUNT(s.id) as sample_count,
                   MIN(s.timestamp) as first_sample,
                   MAX(s.timestamp) as last_sample
            FROM grows g
            LEFT JOIN sensor_data s ON g.id = s.grow_id
            GROUP BY g.id
            ORDER BY g.id DESC
        """
        )

        rows = cursor.fetchall()
        conn.close()

        result = []
        for row in rows:
            grow_dict = dict(row)
            if grow_dict.get("first_sample"):
                grow_dict["first_sample_datetime"] = datetime.fromtimestamp(
                    grow_dict["first_sample"], ARGENTINA_TZ
                ).strftime("%Y-%m-%d %H:%M:%S")
            if grow_dict.get("last_sample"):
                grow_dict["last_sample_datetime"] = datetime.fromtimestamp(
                    grow_dict["last_sample"], ARGENTINA_TZ
                ).strftime("%Y-%m-%d %H:%M:%S")
            result.append(grow_dict)

        return result
    except Exception as e:
        print(f"Error getting all grows: {e}")
        return []


def store_sensor_sample(sensor_data: Dict, grow_id: Optional[int] = None) -> bool:
    """
    Store a single sensor reading in the database.
    """
    try:
        if grow_id is None:
            active_grow = get_active_grow()
            if not active_grow:
                print("No active grow found, cannot store sensor sample")
                return False
            grow_id = int(active_grow["id"])

        current_time = datetime.now(ARGENTINA_TZ)
        current_timestamp = int(current_time.timestamp())

        ensure_schema()
        conn = _open()
        cursor = conn.cursor()

        # Built from one list so the column order and the values cannot drift
        # apart as fields keep being added.
        columns = [
            "temperature", "humidity", "vpd",
            "outside_temperature", "outside_humidity",
            "leaf_temperature", "leaf_vpd", "target_humidity",
            "temperature_min", "temperature_max", "humidity_min", "humidity_max",
            "sample_n", "stage", "indoor_source", "control_action", "quality",
            "temperature_raw", "humidity_raw",
            "outside_temperature_raw", "outside_humidity_raw",
        ]
        placeholders = ", ".join("?" * (len(columns) + 3))
        cursor.execute(
            f"""
            INSERT INTO sensor_data (grow_id, timestamp, datetime, {", ".join(columns)})
            VALUES ({placeholders})
            """,
            (
                grow_id,
                current_timestamp,
                current_time.strftime("%Y-%m-%d %H:%M:%S"),
                *(sensor_data.get(name) for name in columns),
            ),
        )

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error storing sensor sample: {e}")
        return False


def get_calibration(sensor_id: str):
    """
    Correction stored for a sensor, or an identity calibration when there is none.

    Never returns None: the caller applies the result unconditionally, so a
    missing row has to behave like "no correction" rather than force a branch.
    """
    from autocann.control.sampling import Calibration

    try:
        ensure_schema()
        conn = _open(row_factory=True)
        row = conn.execute(
            "SELECT temperature_offset, humidity_offset FROM sensor_calibration WHERE sensor_id = ?",
            (sensor_id,),
        ).fetchone()
        conn.close()
        if row is None:
            return Calibration()
        return Calibration(
            temperature_offset=float(row["temperature_offset"]),
            humidity_offset=float(row["humidity_offset"]),
        )
    except Exception as e:
        print(f"Error getting calibration for {sensor_id}: {e}")
        return Calibration()


def get_all_calibrations() -> List[Dict]:
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        rows = conn.execute(
            "SELECT * FROM sensor_calibration ORDER BY sensor_id"
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Error listing calibrations: {e}")
        return []


def set_calibration(sensor_id: str, temperature_offset: float = 0.0,
                    humidity_offset: float = 0.0, notes: Optional[str] = None) -> bool:
    """Store (or replace) the correction for a sensor."""
    try:
        ensure_schema()
        conn = _open()
        conn.execute(
            """
            INSERT INTO sensor_calibration
                (sensor_id, temperature_offset, humidity_offset, updated_at, notes)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(sensor_id) DO UPDATE SET
                temperature_offset = excluded.temperature_offset,
                humidity_offset = excluded.humidity_offset,
                updated_at = excluded.updated_at,
                notes = excluded.notes
            """,
            (sensor_id, float(temperature_offset), float(humidity_offset),
             int(datetime.now(ARGENTINA_TZ).timestamp()), notes),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error setting calibration for {sensor_id}: {e}")
        return False


def store_control_event(event_type: str, value: str) -> bool:
    """
    Store a control event (humidity up/down, ventilation on/off).
    """
    try:
        current_time = datetime.now(ARGENTINA_TZ)
        current_timestamp = int(current_time.timestamp())

        ensure_schema()
        conn = _open()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO control_events (timestamp, datetime, event_type, value)
            VALUES (?, ?, ?, ?)
        """,
            (
                current_timestamp,
                current_time.strftime("%Y-%m-%d %H:%M:%S"),
                event_type,
                value,
            ),
        )

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error storing control event: {e}")
        return False


def record_stage_event(grow_id: int, stage: str, started_at: Optional[int] = None,
                       notes: Optional[str] = None) -> bool:
    """
    Record that a grow entered a stage.

    Called by create_grow() and update_grow_stage(); `grows.stage` is kept as a
    denormalised copy of the latest value so existing queries keep working.
    """
    try:
        current_time = datetime.now(ARGENTINA_TZ)
        if started_at is None:
            started_at = int(current_time.timestamp())
            stamp = current_time.strftime("%Y-%m-%d %H:%M:%S")
        else:
            stamp = datetime.fromtimestamp(started_at, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S")

        ensure_schema()
        conn = _open()
        conn.execute(
            """
            INSERT INTO stage_events (grow_id, stage, started_at, datetime, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (grow_id, stage, started_at, stamp, notes),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error recording stage event: {e}")
        return False


def get_stage_events(grow_id: int) -> List[Dict]:
    """Every stage change for a grow, oldest first."""
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        rows = conn.execute(
            """
            SELECT id, grow_id, stage, started_at, datetime, notes
            FROM stage_events
            WHERE grow_id = ?
            ORDER BY started_at ASC, id ASC
            """,
            (grow_id,),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Error getting stage events: {e}")
        return []


def get_stage_timeline(grow_id: int, now: Optional[int] = None) -> List[Dict]:
    """Stage periods with their durations, ready for the API."""
    from autocann.control.stages import build_timeline, timeline_to_dicts

    if now is None:
        now = int(datetime.now(ARGENTINA_TZ).timestamp())
    events = get_stage_events(grow_id)
    return timeline_to_dicts(build_timeline(events, now=now))


# ===========================================================================
# Feed chart and irrigation log
# ===========================================================================


def get_feed_plan() -> List[Dict]:
    """
    Every product with its dose grid, in display order.

    Shaped for `autocann.control.feeding`: `doses[stage][week] -> dose`.
    """
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        products = conn.execute(
            "SELECT id, name, unit FROM feed_products ORDER BY position ASC, id ASC"
        ).fetchall()
        doses = conn.execute(
            "SELECT product_id, stage, week, dose FROM feed_plan"
        ).fetchall()
        conn.close()

        by_product: Dict[int, Dict[str, Dict[int, float]]] = {}
        for row in doses:
            by_product.setdefault(row["product_id"], {}).setdefault(row["stage"], {})[
                int(row["week"])
            ] = row["dose"]

        return [
            {
                "id": product["id"],
                "name": product["name"],
                "unit": product["unit"],
                "doses": by_product.get(product["id"], {}),
            }
            for product in products
        ]
    except Exception as e:
        print(f"Error getting feed plan: {e}")
        return []


def replace_feed_plan(products: List[Dict]) -> bool:
    """
    Overwrite the whole chart with `products`.

    A full replace rather than a diff: the editor sends the grid it is showing,
    and renames, reorders, additions and deletions all arrive together. Ids of
    surviving products are preserved so the irrigation log keeps pointing at
    them; a product without an id is new.

    Runs in one transaction, so a rejected row cannot leave half a chart.
    """
    try:
        ensure_schema()
        conn = _open()
        try:
            with conn:  # commits on success, rolls back on exception
                keep = [int(p["id"]) for p in products if p.get("id")]
                if keep:
                    marks = ",".join("?" * len(keep))
                    conn.execute(
                        f"DELETE FROM feed_products WHERE id NOT IN ({marks})", keep
                    )
                else:
                    conn.execute("DELETE FROM feed_products")
                # No ON DELETE CASCADE without PRAGMA foreign_keys, which is off
                # by default in sqlite; clear the orphans explicitly.
                conn.execute(
                    "DELETE FROM feed_plan WHERE product_id NOT IN "
                    "(SELECT id FROM feed_products)"
                )

                for position, product in enumerate(products):
                    name = str(product.get("name") or "").strip() or "Sin nombre"
                    unit = product.get("unit") if product.get("unit") in ("ml/L", "g/L") else "ml/L"
                    product_id = product.get("id")

                    if product_id:
                        conn.execute(
                            "UPDATE feed_products SET name = ?, unit = ?, position = ? WHERE id = ?",
                            (name, unit, position, int(product_id)),
                        )
                    else:
                        cursor = conn.execute(
                            "INSERT INTO feed_products (name, unit, position) VALUES (?, ?, ?)",
                            (name, unit, position),
                        )
                        product_id = cursor.lastrowid

                    conn.execute("DELETE FROM feed_plan WHERE product_id = ?", (product_id,))
                    rows = []
                    for stage, weeks in (product.get("doses") or {}).items():
                        for week, dose in (weeks or {}).items():
                            try:
                                dose = float(dose)
                                week = int(week)
                            except (TypeError, ValueError):
                                continue
                            if dose > 0 and week > 0:
                                rows.append((product_id, stage, week, dose))
                    if rows:
                        conn.executemany(
                            "INSERT INTO feed_plan (product_id, stage, week, dose) "
                            "VALUES (?, ?, ?, ?)",
                            rows,
                        )
        finally:
            conn.close()
        return True
    except Exception as e:
        print(f"Error replacing feed plan: {e}")
        return False


def record_irrigation(grow_id: int, litres: float, items: List[Dict],
                      date: Optional[str] = None, ph: Optional[float] = None,
                      ec: Optional[float] = None, stage: Optional[str] = None,
                      week: Optional[int] = None,
                      notes: str = "") -> Optional[int]:
    """
    Log one watering. Returns its id, or None if it could not be stored.

    `items` are the products actually poured, each `{name, unit, dose}` and
    optionally `product_id` and `on_plan`. An empty list is a plain-water
    watering, which is worth recording during a flush.
    """
    try:
        ensure_schema()
        now = datetime.now(ARGENTINA_TZ)
        if not date:
            date = now.strftime("%Y-%m-%d")

        conn = _open()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO irrigations
                        (grow_id, date, timestamp, litres, ph, ec, stage, week, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (grow_id, date, int(now.timestamp()), float(litres), ph, ec,
                     stage, week, notes or ""),
                )
                irrigation_id = cursor.lastrowid
                rows = []
                for item in items or []:
                    try:
                        dose = float(item.get("dose"))
                    except (TypeError, ValueError):
                        continue
                    if dose <= 0:
                        continue
                    rows.append((
                        irrigation_id,
                        item.get("product_id"),
                        str(item.get("name") or "").strip() or "Sin nombre",
                        item.get("unit") if item.get("unit") in ("ml/L", "g/L") else "ml/L",
                        dose,
                        1 if item.get("on_plan", True) else 0,
                    ))
                if rows:
                    conn.executemany(
                        """
                        INSERT INTO irrigation_items
                            (irrigation_id, product_id, name, unit, dose, on_plan)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
        finally:
            conn.close()
        return irrigation_id
    except Exception as e:
        print(f"Error recording irrigation: {e}")
        return None


def get_irrigations(grow_id: int, limit: int = 100) -> List[Dict]:
    """Waterings for a grow, most recent first, with what went into each."""
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        rows = conn.execute(
            """
            SELECT id, grow_id, date, timestamp, litres, ph, ec, stage, week, notes
            FROM irrigations
            WHERE grow_id = ?
            ORDER BY date DESC, id DESC
            LIMIT ?
            """,
            (grow_id, int(limit)),
        ).fetchall()

        irrigations = [dict(row) for row in rows]
        if irrigations:
            marks = ",".join("?" * len(irrigations))
            items = conn.execute(
                f"""
                SELECT irrigation_id, product_id, name, unit, dose, on_plan
                FROM irrigation_items
                WHERE irrigation_id IN ({marks})
                ORDER BY id ASC
                """,
                [i["id"] for i in irrigations],
            ).fetchall()
            by_irrigation: Dict[int, List[Dict]] = {}
            for item in items:
                entry = dict(item)
                entry["on_plan"] = bool(entry["on_plan"])
                by_irrigation.setdefault(entry.pop("irrigation_id"), []).append(entry)
            for irrigation in irrigations:
                irrigation["items"] = by_irrigation.get(irrigation["id"], [])
        conn.close()
        return irrigations
    except Exception as e:
        print(f"Error getting irrigations: {e}")
        return []


def get_logbook(since_ts: int, limit: int, grow_id: Optional[int] = None) -> List[Dict]:
    """
    Relay changes, waterings and stage changes since `since_ts`, newest first.

    Each entry carries a `source` ("control", "irrigation" or "stage") and the
    raw fields of that source; turning them into sentences is the route's job.

    Waterings and stage changes are scoped to `grow_id` (the active grow): last
    month's grow is not what today's log is about. Control events are global,
    because the relays do not know which grow is in the tent.

    `is_start` is computed over the whole grow, not the window, so a stage
    change inside the window still reads "Pasó a" when the grow began before it.
    """
    entries: List[Dict] = []
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        try:
            for row in conn.execute(
                """
                SELECT timestamp, event_type, value
                FROM control_events
                WHERE timestamp >= ?
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (int(since_ts), int(limit)),
            ).fetchall():
                entries.append({
                    "source": "control",
                    "timestamp": row["timestamp"],
                    "event_type": row["event_type"],
                    "value": row["value"],
                })

            if grow_id is not None:
                irrigation_rows = conn.execute(
                    """
                    SELECT id, timestamp, litres, ph, ec
                    FROM irrigations
                    WHERE grow_id = ? AND timestamp >= ?
                    ORDER BY timestamp DESC, id DESC
                    LIMIT ?
                    """,
                    (int(grow_id), int(since_ts), int(limit)),
                ).fetchall()
                products: Dict[int, List[Dict]] = {}
                if irrigation_rows:
                    marks = ",".join("?" * len(irrigation_rows))
                    for item in conn.execute(
                        f"""
                        SELECT irrigation_id, name, unit, dose
                        FROM irrigation_items
                        WHERE irrigation_id IN ({marks})
                        ORDER BY id ASC
                        """,
                        [r["id"] for r in irrigation_rows],
                    ).fetchall():
                        products.setdefault(item["irrigation_id"], []).append(
                            {"name": item["name"], "unit": item["unit"], "dose": item["dose"]}
                        )
                for row in irrigation_rows:
                    entries.append({
                        "source": "irrigation",
                        "timestamp": row["timestamp"],
                        "litres": row["litres"],
                        "ph": row["ph"],
                        "ec": row["ec"],
                        "items": products.get(row["id"], []),
                    })

                stage_rows = conn.execute(
                    """
                    SELECT id, stage, started_at
                    FROM stage_events
                    WHERE grow_id = ?
                    ORDER BY started_at ASC, id ASC
                    """,
                    (int(grow_id),),
                ).fetchall()
                first_id = stage_rows[0]["id"] if stage_rows else None
                for row in stage_rows:
                    if row["started_at"] < since_ts:
                        continue
                    entries.append({
                        "source": "stage",
                        "timestamp": row["started_at"],
                        "stage": row["stage"],
                        "is_start": row["id"] == first_id,
                    })
        finally:
            conn.close()
    except Exception as e:
        print(f"Error getting logbook: {e}")
        return []

    entries.sort(key=lambda entry: entry["timestamp"], reverse=True)
    return entries[: int(limit)]


def delete_irrigation(irrigation_id: int) -> bool:
    """Remove a watering and everything that was in it."""
    try:
        ensure_schema()
        conn = _open()
        try:
            with conn:
                conn.execute(
                    "DELETE FROM irrigation_items WHERE irrigation_id = ?", (irrigation_id,)
                )
                deleted = conn.execute(
                    "DELETE FROM irrigations WHERE id = ?", (irrigation_id,)
                ).rowcount
        finally:
            conn.close()
        return deleted > 0
    except Exception as e:
        print(f"Error deleting irrigation: {e}")
        return False


def last_irrigation_litres(grow_id: int, default: float = 10.0) -> float:
    """
    How many litres were mixed last time, to pre-fill the next one.

    Saves re-entering the same volume on every watering without needing a
    settings table for it.
    """
    try:
        ensure_schema()
        conn = _open()
        row = conn.execute(
            "SELECT litres FROM irrigations WHERE grow_id = ? ORDER BY id DESC LIMIT 1",
            (grow_id,),
        ).fetchone()
        conn.close()
        return float(row[0]) if row and row[0] else default
    except Exception as e:
        print(f"Error reading last irrigation volume: {e}")
        return default


def get_sensor_data_range(
    start_timestamp: Optional[int] = None,
    end_timestamp: Optional[int] = None,
    limit: Optional[int] = None,
    grow_id=None,
    valid_only: bool = True,
) -> List[Dict]:
    """
    Get sensor data within a time range, for one grow or for all of them.

    `grow_id` follows `_resolve_grow_filter`: an id, `ALL_GROWS`, or None for
    the active grow. Rows holding a physically impossible reading are left out
    unless `valid_only` is False: they are a failed read, not a measurement.
    """
    try:
        grow_id = _resolve_grow_filter(grow_id)
        if grow_id is _NO_GROW:
            return []

        ensure_schema()
        conn = _open(row_factory=True)
        cursor = conn.cursor()

        query = "SELECT * FROM sensor_data WHERE 1=1" + (AND_VALID_ROW if valid_only else "")
        params: List[object] = []

        if grow_id is not None:
            query += " AND grow_id = ?"
            params.append(grow_id)

        if start_timestamp is not None:
            query += " AND timestamp >= ?"
            params.append(start_timestamp)

        if end_timestamp is not None:
            query += " AND timestamp <= ?"
            params.append(end_timestamp)

        query += " ORDER BY timestamp DESC"

        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()

        result = [dict(row) for row in rows]
        conn.close()

        return result
    except Exception as e:
        print(f"Error getting sensor data range: {e}")
        return []


def get_aggregated_data(
    start_timestamp: int,
    end_timestamp: int,
    interval_seconds: int = 3600,
    grow_id=None,
) -> List[Dict]:
    """
    Get aggregated (averaged) sensor data for a time range.

    Across several grows the bucket is split per grow rather than averaged
    together: one bucket holding the mean of two different crops' tents is a
    number about nothing. Each point carries its `grow_id` so a caller can
    break the series where the grow changes.
    """
    try:
        grow_id = _resolve_grow_filter(grow_id)
        if grow_id is _NO_GROW:
            return []

        ensure_schema()
        conn = _open(row_factory=True)
        cursor = conn.cursor()

        query = """
            SELECT
                (timestamp / ?) * ? as interval_start,
                grow_id as grow_id,
                AVG(temperature) as avg_temperature,
                AVG(humidity) as avg_humidity,
                AVG(vpd) as avg_vpd,
                AVG(outside_temperature) as avg_outside_temperature,
                AVG(outside_humidity) as avg_outside_humidity,
                AVG(leaf_temperature) as avg_leaf_temperature,
                AVG(leaf_vpd) as avg_leaf_vpd,
                MIN(temperature) as min_temperature,
                MAX(temperature) as max_temperature,
                MIN(humidity) as min_humidity,
                MAX(humidity) as max_humidity,
                -- True envelope of the bucket: the extremes each stored row saw,
                -- not the extremes of their averages. COALESCE keeps rows
                -- written before the interval summary existed usable.
                MIN(COALESCE(temperature_min, temperature)) as envelope_temp_min,
                MAX(COALESCE(temperature_max, temperature)) as envelope_temp_max,
                MIN(COALESCE(humidity_min, humidity)) as envelope_humidity_min,
                MAX(COALESCE(humidity_max, humidity)) as envelope_humidity_max,
                SUM(COALESCE(sample_n, 1)) as readings_behind,
                COUNT(*) as sample_count
            FROM sensor_data
            WHERE timestamp >= ? AND timestamp <= ?
        """ + AND_VALID_ROW

        params: List[object] = [interval_seconds, interval_seconds, start_timestamp, end_timestamp]

        if grow_id is not None:
            query += " AND grow_id = ?"
            params.append(grow_id)

        # Grouped by grow as well as by bucket. With a single grow that changes
        # nothing; across grows it is what stops one bucket from averaging two
        # crops together.
        query += (" GROUP BY interval_start, grow_id"
                  " ORDER BY interval_start ASC, grow_id ASC")

        cursor.execute(query, params)
        rows = cursor.fetchall()

        result = []
        for row in rows:
            data_point = {
                "timestamp": row["interval_start"],
                "grow_id": row["grow_id"],
                "datetime": datetime.fromtimestamp(row["interval_start"], ARGENTINA_TZ).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "temperature": round(row["avg_temperature"], 2) if row["avg_temperature"] is not None else None,
                "humidity": round(row["avg_humidity"], 2) if row["avg_humidity"] is not None else None,
                "vpd": round(row["avg_vpd"], 2) if row["avg_vpd"] is not None else None,
                "outside_temperature": round(row["avg_outside_temperature"], 2) if row["avg_outside_temperature"] is not None else None,
                "outside_humidity": round(row["avg_outside_humidity"], 2) if row["avg_outside_humidity"] is not None else None,
                "leaf_temperature": round(row["avg_leaf_temperature"], 2) if row["avg_leaf_temperature"] is not None else None,
                "leaf_vpd": round(row["avg_leaf_vpd"], 2) if row["avg_leaf_vpd"] is not None else None,
                "min_temperature": round(row["min_temperature"], 2) if row["min_temperature"] is not None else None,
                "max_temperature": round(row["max_temperature"], 2) if row["max_temperature"] is not None else None,
                "min_humidity": round(row["min_humidity"], 2) if row["min_humidity"] is not None else None,
                "max_humidity": round(row["max_humidity"], 2) if row["max_humidity"] is not None else None,
                # Same key names a raw sample uses, so the charts draw the
                # envelope identically whichever endpoint they are reading.
                "temperature_min": round(row["envelope_temp_min"], 2)
                if row["envelope_temp_min"] is not None else None,
                "temperature_max": round(row["envelope_temp_max"], 2)
                if row["envelope_temp_max"] is not None else None,
                "humidity_min": round(row["envelope_humidity_min"], 2)
                if row["envelope_humidity_min"] is not None else None,
                "humidity_max": round(row["envelope_humidity_max"], 2)
                if row["envelope_humidity_max"] is not None else None,
                "sample_n": row["readings_behind"],
                "sample_count": row["sample_count"],
            }
            result.append(data_point)

        conn.close()
        return result
    except Exception as e:
        print(f"Error getting aggregated data: {e}")
        return []


def get_latest_sensor_data(limit: int = 100, grow_id=None) -> List[Dict]:
    """
    Get the most recent sensor readings.
    """
    return get_sensor_data_range(limit=limit, grow_id=grow_id)


def cleanup_old_data(days_to_keep: int = 90) -> Tuple[int, int]:
    """
    Remove sensor data older than specified days.
    """
    try:
        current_time = datetime.now(ARGENTINA_TZ)
        cutoff_timestamp = int(current_time.timestamp()) - (days_to_keep * 24 * 3600)

        ensure_schema()
        conn = _open()
        cursor = conn.cursor()

        # Delete old sensor data
        cursor.execute("DELETE FROM sensor_data WHERE timestamp < ?", (cutoff_timestamp,))
        sensor_deleted = cursor.rowcount

        # Delete old control events
        cursor.execute("DELETE FROM control_events WHERE timestamp < ?", (cutoff_timestamp,))
        control_deleted = cursor.rowcount

        conn.commit()
        conn.close()

        return (int(sensor_deleted), int(control_deleted))
    except Exception as e:
        print(f"Error cleaning up old data: {e}")
        return (0, 0)


def get_period_summary(
    start_timestamp: int,
    end_timestamp: int,
    grow_id=None,
) -> Dict:
    """
    Get summary statistics (avg, min, max) for a time period.
    """
    try:
        grow_id = _resolve_grow_filter(grow_id)
        if grow_id is _NO_GROW:
            return {"sample_count": 0}

        ensure_schema()
        conn = _open(row_factory=True)
        cursor = conn.cursor()

        query = """
            SELECT
                AVG(temperature) as avg_temp,
                MIN(temperature) as min_temp,
                MAX(temperature) as max_temp,
                AVG(humidity) as avg_humidity,
                MIN(humidity) as min_humidity,
                MAX(humidity) as max_humidity,
                AVG(vpd) as avg_vpd,
                MIN(vpd) as min_vpd,
                MAX(vpd) as max_vpd,
                AVG(outside_temperature) as avg_outside_temp,
                MIN(outside_temperature) as min_outside_temp,
                MAX(outside_temperature) as max_outside_temp,
                AVG(outside_humidity) as avg_outside_humidity,
                AVG(target_humidity) as avg_target_humidity,
                COUNT(*) as sample_count
            FROM sensor_data
            WHERE timestamp >= ? AND timestamp <= ?
        """ + AND_VALID_ROW
        params: List[object] = [start_timestamp, end_timestamp]

        if grow_id is not None:
            query += " AND grow_id = ?"
            params.append(grow_id)

        cursor.execute(query, params)
        row = cursor.fetchone()
        conn.close()

        if row and row["sample_count"] > 0:
            return {
                "temperature": {
                    "avg": round(row["avg_temp"], 1) if row["avg_temp"] is not None else None,
                    "min": round(row["min_temp"], 1) if row["min_temp"] is not None else None,
                    "max": round(row["max_temp"], 1) if row["max_temp"] is not None else None,
                },
                "humidity": {
                    "avg": round(row["avg_humidity"], 1) if row["avg_humidity"] is not None else None,
                    "min": round(row["min_humidity"], 1) if row["min_humidity"] is not None else None,
                    "max": round(row["max_humidity"], 1) if row["max_humidity"] is not None else None,
                },
                "vpd": {
                    "avg": round(row["avg_vpd"], 2) if row["avg_vpd"] is not None else None,
                    "min": round(row["min_vpd"], 2) if row["min_vpd"] is not None else None,
                    "max": round(row["max_vpd"], 2) if row["max_vpd"] is not None else None,
                },
                "outside_temperature": {
                    "avg": round(row["avg_outside_temp"], 1) if row["avg_outside_temp"] is not None else None,
                    "min": round(row["min_outside_temp"], 1) if row["min_outside_temp"] is not None else None,
                    "max": round(row["max_outside_temp"], 1) if row["max_outside_temp"] is not None else None,
                },
                "outside_humidity": {
                    "avg": round(row["avg_outside_humidity"], 1) if row["avg_outside_humidity"] is not None else None,
                },
                "target_humidity": {
                    "avg": round(row["avg_target_humidity"], 1) if row["avg_target_humidity"] is not None else None,
                },
                "sample_count": row["sample_count"],
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
            }
        return {"sample_count": 0}
    except Exception as e:
        print(f"Error getting period summary: {e}")
        return {"error": str(e)}


def get_database_stats() -> Dict:
    """
    Get statistics about the database: file size, row counts and data span.

    Every key here is part of the contract: `autocann.cli.query_db` prints all
    of them and used to crash with a KeyError because this function only
    returned two of them.
    """
    try:
        ensure_schema()
        conn = _open()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM grows")
        grow_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sensor_data")
        sensor_data_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM control_events")
        control_events_count = cursor.fetchone()[0]

        cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM sensor_data")
        oldest_ts, newest_ts = cursor.fetchone()

        conn.close()

        def _as_text(ts: Optional[int]) -> Optional[str]:
            if ts is None:
                return None
            return datetime.fromtimestamp(ts, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S")

        db_size_bytes = DB_PATH.stat().st_size if DB_PATH.exists() else 0

        return {
            "database_path": str(DB_PATH),
            "database_size_mb": round(db_size_bytes / (1024 * 1024), 2),
            "grow_count": grow_count,
            "sensor_data_count": sensor_data_count,
            "control_events_count": control_events_count,
            "oldest_record": _as_text(oldest_ts),
            "newest_record": _as_text(newest_ts),
        }
    except Exception as e:
        print(f"Error getting database stats: {e}")
        return {}


# ===============================
# Analytics Functions
# ===============================



def _is_all_grows(grow_id) -> bool:
    return isinstance(grow_id, str) and grow_id.strip().lower() == ALL_GROWS


def _grow_identity(grow_id: int) -> Tuple[str, str]:
    """`(name, stage)` for a grow, with usable fallbacks if the row is gone."""
    try:
        ensure_schema()
        conn = _open()
        row = conn.execute(
            "SELECT name, stage FROM grows WHERE id = ?", (grow_id,)).fetchone()
        conn.close()
    except Exception as e:
        print(f"Error reading grow {grow_id}: {e}")
        return "Unknown", "early_veg"
    if row is None:
        return "Unknown", "early_veg"
    return row[0], row[1]


#: Refused by the statistics that score readings against a stage's band.
_ALL_GROWS_IS_NOT_A_GROW = (
    "Estas estadísticas son por cultivo: el score mide tiempo dentro de la "
    "banda de una etapa, y cada cultivo pasó por etapas distintas. Elegí uno."
)


def get_vpd_score(
    days: int = 1,
    grow_id=None,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
) -> Dict:
    """
    Calculate VPD score: percentage of time VPD was in optimal range.
    Returns daily scores for the specified number of days.

    If start_ts and end_ts are provided, uses calendar days within that range.
    Otherwise, uses rolling periods of 24 hours.

    `ALL_GROWS` is refused rather than answered: see
    `_ALL_GROWS_IS_NOT_A_GROW`.
    """
    try:
        if _is_all_grows(grow_id):
            return {"error": _ALL_GROWS_IS_NOT_A_GROW}

        resolved = _resolve_grow_filter(grow_id)
        if resolved is _NO_GROW:
            return {"error": "No active grow found"}
        grow_id = resolved
        _, stage = _grow_identity(grow_id)

        vpd_min, vpd_max = VPD_RANGES.get(stage, (0.6, 1.5))

        ensure_schema()
        conn = _open()
        cursor = conn.cursor()

        current_time = datetime.now(ARGENTINA_TZ)
        
        # Use provided timestamps or calculate from days
        if start_ts is not None and end_ts is not None:
            start_timestamp = start_ts
            end_timestamp = end_ts
            # Calculate calendar days
            start_date = datetime.fromtimestamp(start_ts, ARGENTINA_TZ).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            end_date = datetime.fromtimestamp(end_ts, ARGENTINA_TZ)
            days = (end_date.date() - start_date.date()).days + 1
        else:
            end_timestamp = int(current_time.timestamp())
            start_timestamp = end_timestamp - (days * 24 * 3600)
            start_date = None

        daily_scores = []
        
        if start_ts is not None and end_ts is not None:
            # Use calendar days
            current_date = datetime.fromtimestamp(start_ts, ARGENTINA_TZ).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            for _ in range(days):
                day_start = int(current_date.timestamp())
                next_day = current_date + timedelta(days=1)
                day_end = int(next_day.timestamp())
                
                query = """
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN COALESCE(leaf_vpd, vpd) >= ? AND COALESCE(leaf_vpd, vpd) <= ? THEN 1 ELSE 0 END) as in_range
                    FROM sensor_data
                    WHERE timestamp >= ? AND timestamp < ?
                """ + AND_VALID_ROW
                params: List[object] = [vpd_min, vpd_max, day_start, day_end]

                if grow_id is not None:
                    query = query.replace("WHERE", "WHERE grow_id = ? AND")
                    params.insert(2, grow_id)  # Insert after vpd_min, vpd_max

                cursor.execute(query, params)
                row = cursor.fetchone()

                total = row[0] or 0
                in_range = row[1] or 0
                score = round((in_range / total) * 100, 1) if total > 0 else None

                daily_scores.append({
                    "date": current_date.strftime("%Y-%m-%d"),
                    "day_name": current_date.strftime("%A"),
                    "score": score,
                    "samples_total": total,
                    "samples_in_range": in_range,
                })
                current_date = next_day
        else:
            # Use rolling 24-hour periods (original behavior)
            for day_offset in range(days):
                day_start = end_timestamp - ((day_offset + 1) * 24 * 3600)
                day_end = end_timestamp - (day_offset * 24 * 3600)

                query = """
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN COALESCE(leaf_vpd, vpd) >= ? AND COALESCE(leaf_vpd, vpd) <= ? THEN 1 ELSE 0 END) as in_range
                    FROM sensor_data
                    WHERE timestamp >= ? AND timestamp < ?
                """ + AND_VALID_ROW
                params = [vpd_min, vpd_max, day_start, day_end]

                if grow_id is not None:
                    query = query.replace("WHERE", "WHERE grow_id = ? AND")
                    params.insert(2, grow_id)  # Insert after vpd_min, vpd_max

                cursor.execute(query, params)
                row = cursor.fetchone()

                total = row[0] or 0
                in_range = row[1] or 0
                score = round((in_range / total) * 100, 1) if total > 0 else None

                day_date = datetime.fromtimestamp(day_start, ARGENTINA_TZ)
                daily_scores.append({
                    "date": day_date.strftime("%Y-%m-%d"),
                    "day_name": day_date.strftime("%A"),
                    "score": score,
                    "samples_total": total,
                    "samples_in_range": in_range,
                })
            # Reverse to have oldest first
            daily_scores = list(reversed(daily_scores))

        query = """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN COALESCE(leaf_vpd, vpd) >= ? AND COALESCE(leaf_vpd, vpd) <= ? THEN 1 ELSE 0 END) as in_range
            FROM sensor_data
            WHERE timestamp >= ? AND timestamp <= ?
        """ + AND_VALID_ROW
        params = [vpd_min, vpd_max, start_timestamp, end_timestamp]

        if grow_id is not None:
            query = query.replace("WHERE", "WHERE grow_id = ? AND")
            params.insert(2, grow_id)  # Insert after vpd_min, vpd_max

        cursor.execute(query, params)
        row = cursor.fetchone()
        conn.close()

        total = row[0] or 0
        in_range = row[1] or 0
        overall_score = round((in_range / total) * 100, 1) if total > 0 else None

        return {
            "overall_score": overall_score,
            "period_samples_total": total,
            "period_samples_in_range": in_range,
            "vpd_range": {"min": vpd_min, "max": vpd_max},
            "stage": stage,
            "days": days,
            "daily_scores": daily_scores,  # Already oldest first
        }

    except Exception as e:
        print(f"Error calculating VPD score: {e}")
        return {"error": str(e)}


def get_weekly_report(
    grow_id=None,
) -> Dict:
    """
    Generate a comprehensive weekly report with statistics and insights.

    Scoped to one grow, for the reason in `_ALL_GROWS_IS_NOT_A_GROW`. Note that
    the "previous week" comparison is scoped too, so the first week of a grow
    compares against nothing and reports no trend — which is the truth, rather
    than a trend measured against the tent standing empty.
    """
    try:
        if _is_all_grows(grow_id):
            return {"error": _ALL_GROWS_IS_NOT_A_GROW}

        resolved = _resolve_grow_filter(grow_id)
        if resolved is _NO_GROW:
            return {"error": "No active grow found"}
        grow_id = resolved
        grow_name, stage = _grow_identity(grow_id)

        current_time = datetime.now(ARGENTINA_TZ)
        
        # Calculate week boundaries (Monday 00:00 to Sunday 23:59:59 or now)
        days_since_monday = current_time.weekday()  # 0=Monday, 6=Sunday
        week_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = week_start - timedelta(days=days_since_monday)
        
        start_timestamp = int(week_start.timestamp())
        end_timestamp = int(current_time.timestamp())

        summary = get_period_summary(start_timestamp, end_timestamp, grow_id)

        # Get VPD score for this week (using calendar days)
        vpd_score = get_vpd_score(grow_id=grow_id, start_ts=start_timestamp, end_ts=end_timestamp)

        ensure_schema()
        conn = _open()
        cursor = conn.cursor()

        # Get hourly distribution (what hours have best/worst VPD)
        vpd_min, vpd_max = VPD_RANGES.get(stage, (0.6, 1.5))

        query = """
            SELECT 
                CAST(strftime('%H', datetime) AS INTEGER) as hour,
                COUNT(*) as total,
                AVG(temperature) as avg_temp,
                AVG(humidity) as avg_humidity,
                AVG(vpd) as avg_vpd,
                SUM(CASE WHEN COALESCE(leaf_vpd, vpd) >= ? AND COALESCE(leaf_vpd, vpd) <= ? THEN 1 ELSE 0 END) as in_range
            FROM sensor_data
            WHERE timestamp >= ? AND timestamp <= ?
        """ + AND_VALID_ROW
        params: List[object] = [vpd_min, vpd_max, start_timestamp, end_timestamp]

        if grow_id is not None:
            query = query.replace("WHERE", "WHERE grow_id = ? AND")
            params.insert(2, grow_id)  # Insert after vpd_min, vpd_max

        query += " GROUP BY hour ORDER BY hour"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        hourly_stats = []
        best_hour = None
        worst_hour = None
        best_score = -1
        worst_score = 101

        for row in rows:
            hour = row[0]
            total = row[1]
            in_range = row[5]
            score = round((in_range / total) * 100, 1) if total > 0 else 0

            hourly_stats.append({
                "hour": hour,
                "hour_label": f"{hour:02d}:00",
                "avg_temp": round(row[2], 1) if row[2] is not None else None,
                "avg_humidity": round(row[3], 1) if row[3] is not None else None,
                "avg_vpd": round(row[4], 2) if row[4] is not None else None,
                "vpd_score": score,
                "samples": total,
            })

            if score > best_score:
                best_score = score
                best_hour = hour
            if score < worst_score:
                worst_score = score
                worst_hour = hour

        # Compare with previous week (previous Monday to Sunday)
        prev_week_start = week_start - timedelta(days=7)
        prev_week_end = week_start  # Sunday 23:59:59 of previous week
        prev_start = int(prev_week_start.timestamp())
        prev_end = int(prev_week_end.timestamp())

        prev_summary = get_period_summary(prev_start, prev_end, grow_id)
        prev_vpd_score = get_vpd_score(grow_id=grow_id, start_ts=prev_start, end_ts=prev_end)

        conn.close()

        temp_trend = None
        humidity_trend = None
        vpd_trend = None

        if summary.get("temperature", {}).get("avg") and prev_summary.get("temperature", {}).get("avg"):
            temp_trend = round(summary["temperature"]["avg"] - prev_summary["temperature"]["avg"], 1)
        if summary.get("humidity", {}).get("avg") and prev_summary.get("humidity", {}).get("avg"):
            humidity_trend = round(summary["humidity"]["avg"] - prev_summary["humidity"]["avg"], 1)
        if vpd_score.get("overall_score") and prev_vpd_score.get("overall_score"):
            vpd_trend = round(vpd_score["overall_score"] - prev_vpd_score["overall_score"], 1)

        return {
            "grow_name": grow_name,
            "stage": stage,
            "report_period": {
                "start": datetime.fromtimestamp(start_timestamp, ARGENTINA_TZ).strftime("%Y-%m-%d"),
                "end": datetime.fromtimestamp(end_timestamp, ARGENTINA_TZ).strftime("%Y-%m-%d"),
            },
            "summary": {
                "temperature": summary.get("temperature", {}),
                "humidity": summary.get("humidity", {}),
                "vpd": summary.get("vpd", {}),
                "sample_count": summary.get("sample_count", 0),
            },
            "vpd_score": {
                "overall": vpd_score.get("overall_score"),
                "daily": vpd_score.get("daily_scores", []),
                "range": vpd_score.get("vpd_range", {}),
            },
            "trends": {
                "temperature": temp_trend,
                "humidity": humidity_trend,
                "vpd_score": vpd_trend,
            },
            "insights": {
                "best_hour": best_hour,
                "worst_hour": worst_hour,
                "best_hour_score": best_score if best_hour is not None else None,
                "worst_hour_score": worst_score if worst_hour is not None else None,
            },
            "hourly_distribution": hourly_stats,
        }

    except Exception as e:
        print(f"Error generating weekly report: {e}")
        return {"error": str(e)}


def detect_anomalies(
    hours: int = 24,
    grow_id=None,
) -> Dict:
    """
    The stored rows of the last `hours`, run through `control.anomalies`.

    Scoped like the rest, and with no active grow it reports no anomalies at
    all, under `status: "no_grow"`, rather than falling back to every grow.
    Reading a closed grow's last rows as "the tent is fine" would be wrong, but
    so is calling the empty history an outage: without a grow nothing is
    written by design, and that is a state for the dashboard to announce, not
    a fault to raise in red.
    """
    try:
        end_timestamp = int(datetime.now(ARGENTINA_TZ).timestamp())
        start_timestamp = end_timestamp - (hours * 3600)

        resolved = _resolve_grow_filter(grow_id)
        if resolved is _NO_GROW:
            return {
                "status": "no_grow",
                "no_active_grow": True,
                "anomalies": [],
                "warnings": [],
            }
        grow_id = resolved

        ensure_schema()
        conn = _open(row_factory=True)
        cursor = conn.cursor()

        query = """
            SELECT timestamp, datetime, temperature, humidity
            FROM sensor_data
            WHERE timestamp >= ? AND timestamp <= ?
        """
        params: List[object] = [start_timestamp, end_timestamp]
        if grow_id is not None:
            query = query.replace("WHERE", "WHERE grow_id = ? AND")
            params.insert(0, grow_id)
        query += " ORDER BY timestamp ASC"
        rows = [dict(row) for row in cursor.execute(query, params).fetchall()]
        conn.close()

        report = find_anomalies(rows, end_timestamp, hours=hours)
        report["checked_period"] = {
            "hours": hours,
            "samples_checked": len(rows),
            "start": datetime.fromtimestamp(start_timestamp, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "end": datetime.fromtimestamp(end_timestamp, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        }
        return report

    except Exception as e:
        print(f"Error detecting anomalies: {e}")
        return {"error": str(e), "status": "error"}




# ===============================
# Settings
# ===============================


def get_setting(key: str, default=None):
    """
    One stored setting, already parsed. `default` when it is absent or corrupt.

    Corrupt reads as absent on purpose: this is read by the control loop, and a
    bad row must not stop the tent from being controlled.
    """
    ensure_schema()
    try:
        conn = _open()
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return default
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return default


def set_setting(key: str, value) -> bool:
    """Store one setting. Returns whether it was written."""
    ensure_schema()
    try:
        conn = _open()
        try:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, json.dumps(value),
                 datetime.now(ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except sqlite3.Error as e:
        print(f"❌ No se pudo guardar la configuración '{key}': {e}")
        return False
