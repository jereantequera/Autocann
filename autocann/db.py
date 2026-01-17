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


# ===============================
# Analytics Functions
# ===============================

# VPD ranges per stage (same as vpd_math.py)
VPD_RANGES = {
    "early_veg": (0.6, 1.0),
    "late_veg": (0.8, 1.2),
    "flowering": (1.2, 1.5),
    "dry": (0.8, 1.2),  # Same as late_veg for drying
}


def get_vpd_score(
    days: int = 1,
    grow_id: Optional[int] = None,
) -> Dict:
    """
    Calculate VPD score: percentage of time VPD was in optimal range.
    Returns daily scores for the specified number of days.
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
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT stage FROM grows WHERE id = ?", (grow_id,))
            row = cursor.fetchone()
            conn.close()
            stage = row[0] if row else "early_veg"

        vpd_min, vpd_max = VPD_RANGES.get(stage, (0.6, 1.5))

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        current_time = datetime.now(ARGENTINA_TZ)
        end_timestamp = int(current_time.timestamp())
        start_timestamp = end_timestamp - (days * 24 * 3600)

        # Get daily scores
        daily_scores = []
        for day_offset in range(days):
            day_start = end_timestamp - ((day_offset + 1) * 24 * 3600)
            day_end = end_timestamp - (day_offset * 24 * 3600)

            query = """
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN vpd >= ? AND vpd <= ? THEN 1 ELSE 0 END) as in_range
                FROM sensor_data
                WHERE timestamp >= ? AND timestamp < ?
            """
            params: List[object] = [vpd_min, vpd_max, day_start, day_end]

            if grow_id is not None:
                query = query.replace("WHERE", "WHERE grow_id = ? AND")
                params.insert(0, grow_id)

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

        # Calculate overall score
        query = """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN vpd >= ? AND vpd <= ? THEN 1 ELSE 0 END) as in_range
            FROM sensor_data
            WHERE timestamp >= ? AND timestamp <= ?
        """
        params = [vpd_min, vpd_max, start_timestamp, end_timestamp]

        if grow_id is not None:
            query = query.replace("WHERE", "WHERE grow_id = ? AND")
            params.insert(0, grow_id)

        cursor.execute(query, params)
        row = cursor.fetchone()
        conn.close()

        total = row[0] or 0
        in_range = row[1] or 0
        overall_score = round((in_range / total) * 100, 1) if total > 0 else None

        return {
            "overall_score": overall_score,
            "samples_total": total,
            "samples_in_range": in_range,
            "vpd_range": {"min": vpd_min, "max": vpd_max},
            "stage": stage,
            "days": days,
            "daily_scores": list(reversed(daily_scores)),  # Oldest first
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
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT name, stage FROM grows WHERE id = ?", (grow_id,))
            row = cursor.fetchone()
            conn.close()
            grow_name = row[0] if row else "Unknown"
            stage = row[1] if row else "early_veg"

        current_time = datetime.now(ARGENTINA_TZ)
        end_timestamp = int(current_time.timestamp())
        start_timestamp = end_timestamp - (7 * 24 * 3600)

        # Get period summary
        summary = get_period_summary(start_timestamp, end_timestamp, grow_id)

        # Get VPD score
        vpd_score = get_vpd_score(days=7, grow_id=grow_id)

        conn = sqlite3.connect(DB_PATH)
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
                SUM(CASE WHEN vpd >= ? AND vpd <= ? THEN 1 ELSE 0 END) as in_range
            FROM sensor_data
            WHERE timestamp >= ? AND timestamp <= ?
        """
        params: List[object] = [vpd_min, vpd_max, start_timestamp, end_timestamp]

        if grow_id is not None:
            query = query.replace("WHERE", "WHERE grow_id = ? AND")
            params.insert(0, grow_id)

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
                "avg_temp": round(row[2], 1) if row[2] else None,
                "avg_humidity": round(row[3], 1) if row[3] else None,
                "avg_vpd": round(row[4], 2) if row[4] else None,
                "vpd_score": score,
                "samples": total,
            })

            if score > best_score:
                best_score = score
                best_hour = hour
            if score < worst_score:
                worst_score = score
                worst_hour = hour

        # Compare with previous week
        prev_start = start_timestamp - (7 * 24 * 3600)
        prev_end = start_timestamp

        prev_summary = get_period_summary(prev_start, prev_end, grow_id)
        prev_vpd_score = get_vpd_score(days=7, grow_id=grow_id)

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

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
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

        # Check for data gaps (no samples for > 15 minutes when expecting every 5 min)
        expected_interval = 300  # 5 minutes
        max_gap = 900  # 15 minutes (3 missed samples)

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
                    if row["temperature"] and prev_row["temperature"]:
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

                    if row["humidity"] and prev_row["humidity"]:
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


# Initialize database when module is imported
init_database()

