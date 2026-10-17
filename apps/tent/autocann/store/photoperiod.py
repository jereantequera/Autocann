"""
Photoperiod per grow: the local hours the lights come on and go off.

A window runs from `on` up to `off`, and crosses midnight when `off` is the
smaller hour: on 18, off 12 means the lights are on from 18:00 until 12:00 the
next day, 18 hours. `off = 0` is midnight. A grow that never set one gets the
defaults below, which is what the tent ran before the column existed.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from autocann.store.connection import _open
from autocann.store.schema import ensure_schema

#: The lights come on at 06:00 and go off at midnight: 18 hours of light.
#: Matches the simulator's history, so a fresh grow and its synthetic past agree.
DEFAULT_LIGHTS_ON_HOUR = 6
DEFAULT_LIGHTS_OFF_HOUR = 0


def validate_photoperiod(on, off) -> Tuple[int, int]:
    """
    Check a photoperiod and return it as ints.

    Raises ValueError with the reason. Booleans are refused even though they are
    ints in Python: `true` in a JSON body is not an hour.
    """
    for name, value in (("on", on), ("off", off)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"'{name}' must be an integer hour from 0 to 23")
        if not 0 <= value <= 23:
            raise ValueError(f"'{name}' must be an hour from 0 to 23")
    if on == off:
        raise ValueError("'on' and 'off' must differ: the lights need a window")
    return on, off


def lights_hours(on: int, off: int) -> int:
    """How many hours the lights are on in one day for this window."""
    return (off - on) % 24


def get_photoperiod(grow_id: int) -> Optional[Dict]:
    """
    The grow's photoperiod, or None when no such grow exists.

    `configured` says whether the grow set its own hours; when it did not, `on`
    and `off` are the defaults.
    """
    ensure_schema()
    conn = _open()
    try:
        row = conn.execute(
            "SELECT lights_on_hour, lights_off_hour FROM grows WHERE id = ?", (grow_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    configured = row[0] is not None and row[1] is not None
    return {
        "on": row[0] if configured else DEFAULT_LIGHTS_ON_HOUR,
        "off": row[1] if configured else DEFAULT_LIGHTS_OFF_HOUR,
        "configured": configured,
    }


def set_photoperiod(grow_id: int, on, off) -> Optional[Dict]:
    """
    Store a grow's photoperiod. Returns the saved value, or None when the grow
    does not exist. Raises ValueError for hours that cannot be a window.
    """
    on, off = validate_photoperiod(on, off)
    ensure_schema()
    conn = _open()
    try:
        cursor = conn.execute(
            "UPDATE grows SET lights_on_hour = ?, lights_off_hour = ? WHERE id = ?",
            (on, off, grow_id),
        )
        conn.commit()
        updated = cursor.rowcount
    finally:
        conn.close()
    if not updated:
        return None
    return {"on": on, "off": off, "configured": True}


def photoperiod_or_default(grow_id: int) -> Tuple[int, int]:
    """`(on, off)` for a grow, falling back to the defaults for a missing grow."""
    found = get_photoperiod(grow_id)
    if found is None:
        return DEFAULT_LIGHTS_ON_HOUR, DEFAULT_LIGHTS_OFF_HOUR
    return found["on"], found["off"]
