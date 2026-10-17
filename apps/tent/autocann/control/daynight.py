"""
Day and night: which samples were taken with the lights on.

Pure functions. The clock is passed in as a number of hours and a timezone, so
nothing here reads the time or the database. The window comes from the grow's
photoperiod (see `store/photoperiod.py`) and may cross midnight.
"""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Iterable, List, Tuple


def is_lights_on(local_hour: float, on: int, off: int) -> bool:
    """
    Whether the lights are on at `local_hour` (0 <= hour < 24, fractions allowed).

    The window is half-open: the lights are on at `on` and off at `off`. Measured
    from `on` the window is `(off - on) % 24` hours long, which handles both a
    window inside one day (on 6, off 18) and one that crosses midnight (on 18,
    off 12). `on == off` is not a window and is refused.
    """
    if on == off:
        raise ValueError("on and off must differ")
    duration = (off - on) % 24
    return (local_hour - on) % 24.0 < duration


def local_hour_of(timestamp: int, tz: tzinfo) -> float:
    """The clock of an epoch second in `tz`, as hours with the minutes as a fraction."""
    moment = datetime.fromtimestamp(timestamp, tz)
    return moment.hour + moment.minute / 60.0 + moment.second / 3600.0


def split_rows(rows: Iterable[dict], on: int, off: int, tz: tzinfo) -> Tuple[List[dict], List[dict]]:
    """
    Divide sample rows into `(day_rows, night_rows)`, keeping their order.

    Each row needs a `timestamp` (epoch seconds). Rows are not copied or
    changed: a caller can compute over either list with the same fields.
    """
    day: List[dict] = []
    night: List[dict] = []
    for row in rows:
        target = day if is_lights_on(local_hour_of(row["timestamp"], tz), on, off) else night
        target.append(row)
    return day, night
