#!/usr/bin/env python

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from autocann.control.vpd_math import VPD_RANGES
from autocann.paths import DB_PATH
from autocann.time import ARGENTINA_TZ

#: Wait this long for a competing writer before raising "database is locked".
#: The control loop and the web app both write, so they do collide.
BUSY_TIMEOUT_SECONDS = 10.0

_schema_ready = False


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

    conn.commit()

    _migrate_nullable_outdoor_columns(conn)
    _migrate_sample_detail_columns(conn)
    _backfill_stage_events(conn)

    # Create default grow if none exists
    cursor.execute("SELECT COUNT(*) FROM grows")
    count = cursor.fetchone()[0]
    if count == 0:
        current_time = datetime.now(ARGENTINA_TZ)
        cursor.execute(
            """
            INSERT INTO grows (name, stage, start_date, is_active, notes)
            VALUES (?, ?, ?, 1, ?)
        """,
            (
                "Cultivo #1",
                "early_veg",
                current_time.strftime("%Y-%m-%d %H:%M:%S"),
                "Cultivo inicial creado automáticamente",
            ),
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
        # Get grow_id if not provided
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


def get_sensor_data_range(
    start_timestamp: Optional[int] = None,
    end_timestamp: Optional[int] = None,
    limit: Optional[int] = None,
    grow_id: Optional[int] = None,
) -> List[Dict]:
    """
    Get sensor data within a time range.
    """
    try:
        # Get grow_id if not provided
        if grow_id is None:
            active_grow = get_active_grow()
            if active_grow:
                grow_id = int(active_grow["id"])

        ensure_schema()
        conn = _open(row_factory=True)
        cursor = conn.cursor()

        query = "SELECT * FROM sensor_data WHERE 1=1"
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
    grow_id: Optional[int] = None,
) -> List[Dict]:
    """
    Get aggregated (averaged) sensor data for a time range.
    """
    try:
        # Get grow_id if not provided
        if grow_id is None:
            active_grow = get_active_grow()
            if active_grow:
                grow_id = int(active_grow["id"])

        ensure_schema()
        conn = _open(row_factory=True)
        cursor = conn.cursor()

        query = """
            SELECT
                (timestamp / ?) * ? as interval_start,
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
        """

        params: List[object] = [interval_seconds, interval_seconds, start_timestamp, end_timestamp]

        if grow_id is not None:
            query += " AND grow_id = ?"
            params.append(grow_id)

        query += " GROUP BY interval_start ORDER BY interval_start ASC"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        result = []
        for row in rows:
            data_point = {
                "timestamp": row["interval_start"],
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


def get_latest_sensor_data(limit: int = 100, grow_id: Optional[int] = None) -> List[Dict]:
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
    grow_id: Optional[int] = None,
) -> Dict:
    """
    Get summary statistics (avg, min, max) for a time period.
    """
    try:
        # Get grow_id if not provided
        if grow_id is None:
            active_grow = get_active_grow()
            if active_grow:
                grow_id = int(active_grow["id"])

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
        """
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



def get_vpd_score(
    days: int = 1,
    grow_id: Optional[int] = None,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
) -> Dict:
    """
    Calculate VPD score: percentage of time VPD was in optimal range.
    Returns daily scores for the specified number of days.
    
    If start_ts and end_ts are provided, uses calendar days within that range.
    Otherwise, uses rolling periods of 24 hours.
    """
    try:
        if grow_id is None:
            active_grow = get_active_grow()
            if active_grow:
                grow_id = int(active_grow["id"])
                stage = active_grow.get("stage", "early_veg")
            else:
                return {"error": "No active grow found"}
        else:
            # Get stage for specified grow
            ensure_schema()
            conn = _open()
            cursor = conn.cursor()
            cursor.execute("SELECT stage FROM grows WHERE id = ?", (grow_id,))
            row = cursor.fetchone()
            conn.close()
            stage = row[0] if row else "early_veg"

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

        # Get daily scores
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
                """
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
                """
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

        # Calculate overall score
        query = """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN COALESCE(leaf_vpd, vpd) >= ? AND COALESCE(leaf_vpd, vpd) <= ? THEN 1 ELSE 0 END) as in_range
            FROM sensor_data
            WHERE timestamp >= ? AND timestamp <= ?
        """
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
    grow_id: Optional[int] = None,
) -> Dict:
    """
    Generate a comprehensive weekly report with statistics and insights.
    """
    try:
        if grow_id is None:
            active_grow = get_active_grow()
            if active_grow:
                grow_id = int(active_grow["id"])
                grow_name = active_grow.get("name", "Unknown")
                stage = active_grow.get("stage", "early_veg")
            else:
                return {"error": "No active grow found"}
        else:
            ensure_schema()
            conn = _open()
            cursor = conn.cursor()
            cursor.execute("SELECT name, stage FROM grows WHERE id = ?", (grow_id,))
            row = cursor.fetchone()
            conn.close()
            grow_name = row[0] if row else "Unknown"
            stage = row[1] if row else "early_veg"

        current_time = datetime.now(ARGENTINA_TZ)
        
        # Calculate week boundaries (Monday 00:00 to Sunday 23:59:59 or now)
        days_since_monday = current_time.weekday()  # 0=Monday, 6=Sunday
        week_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = week_start - timedelta(days=days_since_monday)
        
        start_timestamp = int(week_start.timestamp())
        end_timestamp = int(current_time.timestamp())

        # Get period summary
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
        """
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

        # Calculate trends
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
    grow_id: Optional[int] = None,
) -> Dict:
    """
    Detect anomalies in sensor data:
    - Sensor disconnected (no data for extended period)
    - Sudden spikes/drops in temperature or humidity
    - Values outside physically possible ranges
    - Stuck values (sensor malfunction)
    """
    try:
        if grow_id is None:
            active_grow = get_active_grow()
            if active_grow:
                grow_id = int(active_grow["id"])

        anomalies = []
        warnings = []

        current_time = datetime.now(ARGENTINA_TZ)
        end_timestamp = int(current_time.timestamp())
        start_timestamp = end_timestamp - (hours * 3600)

        ensure_schema()
        conn = _open(row_factory=True)
        cursor = conn.cursor()

        # Query to get data ordered by timestamp
        query = """
            SELECT timestamp, datetime, temperature, humidity, vpd, 
                   outside_temperature, outside_humidity
            FROM sensor_data
            WHERE timestamp >= ? AND timestamp <= ?
        """
        params: List[object] = [start_timestamp, end_timestamp]

        if grow_id is not None:
            query = query.replace("WHERE", "WHERE grow_id = ? AND")
            params.insert(0, grow_id)

        query += " ORDER BY timestamp ASC"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        if len(rows) == 0:
            anomalies.append({
                "type": "no_data",
                "severity": "critical",
                "message": f"No hay datos en las últimas {hours} horas",
                "timestamp": current_time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            conn.close()
            return {"anomalies": anomalies, "warnings": warnings, "status": "critical"}

        # Samples are written every 5 minutes, so flag a gap of 3 missed samples.
        max_gap = 900

        prev_timestamp = None
        for row in rows:
            if prev_timestamp is not None:
                gap = row["timestamp"] - prev_timestamp
                if gap > max_gap:
                    gap_minutes = gap // 60
                    gap_time = datetime.fromtimestamp(prev_timestamp, ARGENTINA_TZ)
                    warnings.append({
                        "type": "data_gap",
                        "severity": "warning",
                        "message": f"Sin datos por {gap_minutes} minutos",
                        "timestamp": gap_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "gap_minutes": gap_minutes,
                    })
            prev_timestamp = row["timestamp"]

        # Check time since last sample
        last_sample_time = rows[-1]["timestamp"]
        time_since_last = end_timestamp - last_sample_time
        if time_since_last > max_gap:
            minutes_ago = time_since_last // 60
            anomalies.append({
                "type": "stale_data",
                "severity": "critical",
                "message": f"Último dato hace {minutes_ago} minutos - sensor posiblemente desconectado",
                "timestamp": datetime.fromtimestamp(last_sample_time, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "minutes_ago": minutes_ago,
            })

        # Check for physically impossible values
        for row in rows:
            # Temperature checks (realistic range: -10 to 60°C)
            if row["temperature"] is not None:
                if row["temperature"] < -10 or row["temperature"] > 60:
                    anomalies.append({
                        "type": "invalid_temperature",
                        "severity": "critical",
                        "message": f"Temperatura inválida: {row['temperature']}°C",
                        "timestamp": row["datetime"],
                        "value": row["temperature"],
                    })

            # Humidity checks (0-100%)
            if row["humidity"] is not None:
                if row["humidity"] < 0 or row["humidity"] > 100:
                    anomalies.append({
                        "type": "invalid_humidity",
                        "severity": "critical",
                        "message": f"Humedad inválida: {row['humidity']}%",
                        "timestamp": row["datetime"],
                        "value": row["humidity"],
                    })

        # Check for sudden spikes (change > 10°C or 30% in 5 minutes)
        temp_threshold = 10  # °C
        humidity_threshold = 30  # %

        prev_row = None
        for row in rows:
            if prev_row is not None:
                time_diff = row["timestamp"] - prev_row["timestamp"]
                if time_diff <= 600:  # Within 10 minutes
                    if row["temperature"] is not None and prev_row["temperature"] is not None:
                        temp_change = abs(row["temperature"] - prev_row["temperature"])
                        if temp_change > temp_threshold:
                            warnings.append({
                                "type": "temperature_spike",
                                "severity": "warning",
                                "message": f"Cambio brusco de temperatura: {temp_change:.1f}°C en {time_diff // 60} min",
                                "timestamp": row["datetime"],
                                "change": temp_change,
                                "from_value": prev_row["temperature"],
                                "to_value": row["temperature"],
                            })

                    if row["humidity"] is not None and prev_row["humidity"] is not None:
                        humidity_change = abs(row["humidity"] - prev_row["humidity"])
                        if humidity_change > humidity_threshold:
                            warnings.append({
                                "type": "humidity_spike",
                                "severity": "warning",
                                "message": f"Cambio brusco de humedad: {humidity_change:.1f}% en {time_diff // 60} min",
                                "timestamp": row["datetime"],
                                "change": humidity_change,
                                "from_value": prev_row["humidity"],
                                "to_value": row["humidity"],
                            })
            prev_row = row

        # Check for stuck values (same value for > 30 minutes = sensor malfunction)
        stuck_threshold = 6  # 6 samples of 5 min = 30 minutes

        temp_values = [r["temperature"] for r in rows if r["temperature"] is not None]
        humidity_values = [r["humidity"] for r in rows if r["humidity"] is not None]

        def check_stuck(values, name):
            if len(values) < stuck_threshold:
                return None
            for i in range(len(values) - stuck_threshold + 1):
                window = values[i:i + stuck_threshold]
                if len(set(window)) == 1:  # All values identical
                    return {
                        "type": f"stuck_{name}",
                        "severity": "warning",
                        "message": f"{name.capitalize()} estancado en {window[0]} por >30 min - posible fallo de sensor",
                        "value": window[0],
                    }
            return None

        stuck_temp = check_stuck(temp_values, "temperature")
        if stuck_temp:
            warnings.append(stuck_temp)

        stuck_humidity = check_stuck(humidity_values, "humidity")
        if stuck_humidity:
            warnings.append(stuck_humidity)

        conn.close()

        # Determine overall status
        if anomalies:
            status = "critical"
        elif warnings:
            status = "warning"
        else:
            status = "ok"

        return {
            "status": status,
            "anomalies": anomalies,
            "warnings": warnings,
            "checked_period": {
                "hours": hours,
                "samples_checked": len(rows),
                "start": datetime.fromtimestamp(start_timestamp, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "end": datetime.fromtimestamp(end_timestamp, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            },
        }

    except Exception as e:
        print(f"Error detecting anomalies: {e}")
        return {"error": str(e), "status": "error"}


