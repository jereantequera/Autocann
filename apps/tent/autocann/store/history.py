"""
Aggregated history, period summaries and database stats for the dashboard.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from autocann.control.plausible import AND_VALID_ROW, OUTSIDE_HUMIDITY_SQL, OUTSIDE_TEMPERATURE_SQL
from autocann.store import connection
from autocann.store.connection import _open
from autocann.store.day_night import day_night_summaries
from autocann.store.grows import _NO_GROW, _resolve_grow_filter
from autocann.store.schema import ensure_schema
from autocann.time import ARGENTINA_TZ


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

        query = f"""
            SELECT
                (timestamp / ?) * ? as interval_start,
                grow_id as grow_id,
                AVG(temperature) as avg_temperature,
                AVG(humidity) as avg_humidity,
                AVG(vpd) as avg_vpd,
                AVG({OUTSIDE_TEMPERATURE_SQL}) as avg_outside_temperature,
                AVG({OUTSIDE_HUMIDITY_SQL}) as avg_outside_humidity,
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

        query = f"""
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
                AVG({OUTSIDE_TEMPERATURE_SQL}) as avg_outside_temp,
                MIN({OUTSIDE_TEMPERATURE_SQL}) as min_outside_temp,
                MAX({OUTSIDE_TEMPERATURE_SQL}) as max_outside_temp,
                AVG({OUTSIDE_HUMIDITY_SQL}) as avg_outside_humidity,
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
            result = {
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
            # Added beside the fields above, never in place of them: a caller
            # that does not know about day and night reads the same numbers.
            result.update(day_night_summaries(start_timestamp, end_timestamp, grow_id))
            return result
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

        db_size_bytes = connection.DB_PATH.stat().st_size if connection.DB_PATH.exists() else 0

        return {
            "database_path": str(connection.DB_PATH),
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
