#!/usr/bin/env python

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pytz

from autocann.paths import DB_PATH
from autocann.time import ARGENTINA_TZ


def init_database() -> None:
    """
    Initialize the database and create tables if they don't exist.
    """
    # Create data directory if it doesn't exist
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
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
            outside_temperature REAL NOT NULL,
            outside_humidity REAL NOT NULL,
            leaf_temperature REAL,
            leaf_vpd REAL,
            target_humidity REAL,
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

    conn.commit()

    # Create default grow if none exists
    cursor.execute("SELECT COUNT(*) FROM grows")
    count = cursor.fetchone()[0]
    if count == 0:
        argentina_tz = pytz.timezone("America/Argentina/Buenos_Aires")
        current_time = datetime.now(argentina_tz)
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
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
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

        if row:
            return dict(row)
        return None
    except Exception as e:
        print(f"Error getting active grow: {e}")
        return None


def create_grow(name: str, stage: str = "early_veg", notes: str = "") -> Optional[int]:
    """
    Create a new grow and set it as active.
    """
    try:
        argentina_tz = pytz.timezone("America/Argentina/Buenos_Aires")
        current_time = datetime.now(argentina_tz)

        conn = sqlite3.connect(DB_PATH)
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

        return int(grow_id) if grow_id is not None else None
    except Exception as e:
        print(f"Error creating grow: {e}")
        return None


def end_grow(grow_id: int) -> bool:
    """
    End a grow by setting its end date and deactivating it.
    """
    try:
        argentina_tz = pytz.timezone("America/Argentina/Buenos_Aires")
        current_time = datetime.now(argentina_tz)

        conn = sqlite3.connect(DB_PATH)
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
        conn = sqlite3.connect(DB_PATH)
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


def update_grow_stage(grow_id: int, stage: str) -> bool:
    """
    Update the stage of a grow.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("UPDATE grows SET stage = ? WHERE id = ?", (stage, grow_id))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error updating grow stage: {e}")
        return False


def get_all_grows() -> List[Dict]:
    """
    Get all grows.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
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

        argentina_tz = pytz.timezone("America/Argentina/Cordoba")
        current_time = datetime.now(argentina_tz)
        current_timestamp = int(current_time.timestamp())

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO sensor_data (
                grow_id, timestamp, datetime, temperature, humidity, vpd,
                outside_temperature, outside_humidity,
                leaf_temperature, leaf_vpd, target_humidity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                grow_id,
                current_timestamp,
                current_time.strftime("%Y-%m-%d %H:%M:%S"),
                sensor_data.get("temperature"),
                sensor_data.get("humidity"),
                sensor_data.get("vpd"),
                sensor_data.get("outside_temperature"),
                sensor_data.get("outside_humidity"),
                sensor_data.get("leaf_temperature"),
                sensor_data.get("leaf_vpd"),
                sensor_data.get("target_humidity"),
            ),
        )

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error storing sensor sample: {e}")
        return False


def store_control_event(event_type: str, value: str) -> bool:
    """
    Store a control event (humidity up/down, ventilation on/off).
    """
    try:
        argentina_tz = pytz.timezone("America/Argentina/Cordoba")
        current_time = datetime.now(argentina_tz)
        current_timestamp = int(current_time.timestamp())

        conn = sqlite3.connect(DB_PATH)
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

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
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

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
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
                "temperature": round(row["avg_temperature"], 2) if row["avg_temperature"] else None,
                "humidity": round(row["avg_humidity"], 2) if row["avg_humidity"] else None,
                "vpd": round(row["avg_vpd"], 2) if row["avg_vpd"] else None,
                "outside_temperature": round(row["avg_outside_temperature"], 2)
                if row["avg_outside_temperature"]
                else None,
                "outside_humidity": round(row["avg_outside_humidity"], 2) if row["avg_outside_humidity"] else None,
                "leaf_temperature": round(row["avg_leaf_temperature"], 2) if row["avg_leaf_temperature"] else None,
                "leaf_vpd": round(row["avg_leaf_vpd"], 2) if row["avg_leaf_vpd"] else None,
                "min_temperature": round(row["min_temperature"], 2) if row["min_temperature"] else None,
                "max_temperature": round(row["max_temperature"], 2) if row["max_temperature"] else None,
                "min_humidity": round(row["min_humidity"], 2) if row["min_humidity"] else None,
                "max_humidity": round(row["max_humidity"], 2) if row["max_humidity"] else None,
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
        argentina_tz = pytz.timezone("America/Argentina/Cordoba")
        current_time = datetime.now(argentina_tz)
        cutoff_timestamp = int(current_time.timestamp()) - (days_to_keep * 24 * 3600)

        conn = sqlite3.connect(DB_PATH)
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

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
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
                    "avg": round(row["avg_temp"], 1) if row["avg_temp"] else None,
                    "min": round(row["min_temp"], 1) if row["min_temp"] else None,
                    "max": round(row["max_temp"], 1) if row["max_temp"] else None,
                },
                "humidity": {
                    "avg": round(row["avg_humidity"], 1) if row["avg_humidity"] else None,
                    "min": round(row["min_humidity"], 1) if row["min_humidity"] else None,
                    "max": round(row["max_humidity"], 1) if row["max_humidity"] else None,
                },
                "vpd": {
                    "avg": round(row["avg_vpd"], 2) if row["avg_vpd"] else None,
                    "min": round(row["min_vpd"], 2) if row["min_vpd"] else None,
                    "max": round(row["max_vpd"], 2) if row["max_vpd"] else None,
                },
                "outside_temperature": {
                    "avg": round(row["avg_outside_temp"], 1) if row["avg_outside_temp"] else None,
                    "min": round(row["min_outside_temp"], 1) if row["min_outside_temp"] else None,
                    "max": round(row["max_outside_temp"], 1) if row["max_outside_temp"] else None,
                },
                "outside_humidity": {
                    "avg": round(row["avg_outside_humidity"], 1) if row["avg_outside_humidity"] else None,
                },
                "target_humidity": {
                    "avg": round(row["avg_target_humidity"], 1) if row["avg_target_humidity"] else None,
                },
                "sample_count": row["sample_count"],
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
            }
        return {"sample_count": 0}
    except Exception as e:
        print(f"Error getting period summary: {e}")
        return {"error": str(e)}


def get_database_stats(grow_id: Optional[int] = None) -> Dict:
    """
    Get statistics about the database.
    """
    try:
        # Get grow_id if not provided
        if grow_id is None:
            active_grow = get_active_grow()
            if active_grow:
                grow_id = int(active_grow["id"])

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Get sensor data stats (filtered by grow if provided)
        if grow_id is not None:
            cursor.execute("SELECT COUNT(*) FROM sensor_data WHERE grow_id = ?", (grow_id,))
            sensor_count = cursor.fetchone()[0]

            cursor.execute(
                "SELECT MIN(timestamp), MAX(timestamp) FROM sensor_data WHERE grow_id = ?",
                (grow_id,),
            )
            min_ts, max_ts = cursor.fetchone()
        else:
            cursor.execute("SELECT COUNT(*) FROM sensor_data")
            sensor_count = cursor.fetchone()[0]

            cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM sensor_data")
            min_ts, max_ts = cursor.fetchone()

        # Get control events stats
        cursor.execute("SELECT COUNT(*) FROM control_events")
        control_count = cursor.fetchone()[0]

        # Get grow count
        cursor.execute("SELECT COUNT(*) FROM grows")
        grow_count = cursor.fetchone()[0]

        # Get database file size
        db_size_bytes = DB_PATH.stat().st_size if DB_PATH.exists() else 0
        db_size_mb = round(db_size_bytes / (1024 * 1024), 2)

        conn.close()

        return {
            "sensor_data_count": sensor_count,
            "control_events_count": control_count,
            "grow_count": grow_count,
            "oldest_record": datetime.fromtimestamp(min_ts, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
            if min_ts
            else None,
            "newest_record": datetime.fromtimestamp(max_ts, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
            if max_ts
            else None,
            "database_size_mb": db_size_mb,
            "database_path": str(DB_PATH),
        }
    except Exception as e:
        print(f"Error getting database stats: {e}")
        return {}


# Initialize database when module is imported
init_database()

