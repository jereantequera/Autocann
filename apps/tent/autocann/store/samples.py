"""
Sensor samples and control events: writes from the control loop and raw reads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from autocann.control.plausible import AND_VALID_ROW, scrub_outdoor
from autocann.store.active_grow import get_active_grow
from autocann.store.connection import _open
from autocann.store.grows import _NO_GROW, _resolve_grow_filter
from autocann.store.schema import ensure_schema
from autocann.time import ARGENTINA_TZ


def store_sensor_sample(sensor_data: Dict, grow_id: Optional[int] = None,
                        at: Optional[int] = None) -> bool:
    """
    Store a single sensor reading in the database.

    `at` is the epoch second the reading is stamped with; the loop leaves it
    out and gets the current time. Only the simulator's history passes it.
    """
    try:
        if grow_id is None:
            active_grow = get_active_grow()
            if not active_grow:
                print("No active grow found, cannot store sensor sample")
                return False
            grow_id = int(active_grow["id"])

        current_time = datetime.now(ARGENTINA_TZ) if at is None else datetime.fromtimestamp(at, ARGENTINA_TZ)
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


def get_latest_sensor_data(limit: int = 100, grow_id=None) -> List[Dict]:
    """
    Get the most recent sensor readings.
    """
    return get_sensor_data_range(limit=limit, grow_id=grow_id)


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

        result = [scrub_outdoor(dict(row)) if valid_only else dict(row) for row in rows]
        conn.close()

        return result
    except Exception as e:
        print(f"Error getting sensor data range: {e}")
        return []


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
