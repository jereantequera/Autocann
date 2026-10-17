"""
The watering interval of each grow, and the date of its last watering.

The interval is a column on `grows` (NULL means not configured). The next
watering is not stored anywhere: `autocann.control.irrigation.next_due` works it
out from the last one.
"""

from __future__ import annotations

from typing import Optional

from autocann.store.connection import _open
from autocann.store.schema import ensure_schema


def get_irrigation_interval(grow_id: int) -> Optional[int]:
    """The grow's interval in days, or None when it is not configured."""
    ensure_schema()
    conn = _open()
    try:
        row = conn.execute(
            "SELECT irrigation_interval_days FROM grows WHERE id = ?", (int(grow_id),)
        ).fetchone()
    finally:
        conn.close()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def set_irrigation_interval(grow_id: int, days: Optional[int]) -> bool:
    """
    Set the grow's interval; None clears it. Returns False if there is no such grow.

    Validation is the caller's job: the API accepts 1 to 30 or null.
    """
    ensure_schema()
    conn = _open()
    try:
        with conn:
            cursor = conn.execute(
                "UPDATE grows SET irrigation_interval_days = ? WHERE id = ?",
                (None if days is None else int(days), int(grow_id)),
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


def last_irrigation_date(grow_id: int) -> Optional[str]:
    """The most recent watering date of a grow ('YYYY-MM-DD'), or None if none."""
    ensure_schema()
    conn = _open()
    try:
        row = conn.execute(
            "SELECT MAX(date) FROM irrigations WHERE grow_id = ?", (int(grow_id),)
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row and row[0] else None
