"""
Per-sensor calibration offsets.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from autocann.store.connection import _open
from autocann.store.schema import ensure_schema
from autocann.time import ARGENTINA_TZ


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
