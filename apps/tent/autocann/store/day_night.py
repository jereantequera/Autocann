"""
Day and night sub-summaries of a period: the same numbers as the period
summary, computed over the samples taken with the lights on and off.

Each row is classified with the photoperiod of its own grow, so a window that
covers several grows still splits every row correctly. Time in range is only
given for one grow: it is judged against that grow's stage band, the same
restriction the VPD score has.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from autocann.control.daynight import split_rows
from autocann.control.plausible import AND_VALID_ROW, OUTSIDE_HUMIDITY_SQL, OUTSIDE_TEMPERATURE_SQL
from autocann.store.connection import _open
from autocann.store.grow_stages import grow_vpd_band
from autocann.store.photoperiod import photoperiod_or_default
from autocann.store.schema import ensure_schema
from autocann.store.vpd_score import _grow_identity
from autocann.time import ARGENTINA_TZ


def _stat(values: List[Optional[float]], digits: int) -> Dict:
    present = [value for value in values if value is not None]
    if not present:
        return {"avg": None, "min": None, "max": None}
    return {
        "avg": round(sum(present) / len(present), digits),
        "min": round(min(present), digits),
        "max": round(max(present), digits),
    }


def _avg(values: List[Optional[float]], digits: int) -> Dict:
    present = [value for value in values if value is not None]
    return {"avg": round(sum(present) / len(present), digits) if present else None}


def _time_in_range(rows: List[dict], band: Optional[Tuple[float, float]]) -> Optional[Dict]:
    """The share of rows whose leaf VPD (or air VPD, when there is no leaf) sits in the band."""
    if band is None:
        return None
    low, high = band
    values = [
        row["leaf_vpd"] if row["leaf_vpd"] is not None else row["vpd"]
        for row in rows
    ]
    values = [value for value in values if value is not None]
    inside = sum(1 for value in values if low <= value <= high)
    total = len(values)
    return {
        "percent": round(inside / total * 100, 1) if total else None,
        "in_range": inside,
        "total": total,
    }


def _sub_summary(rows: List[dict], band: Optional[Tuple[float, float]]) -> Dict:
    """The period summary's fields for one part of the day, or a zero count."""
    if not rows:
        return {"sample_count": 0}
    return {
        "temperature": _stat([r["temperature"] for r in rows], 1),
        "humidity": _stat([r["humidity"] for r in rows], 1),
        "vpd": _stat([r["vpd"] for r in rows], 2),
        "outside_temperature": _stat([r["outside_temperature"] for r in rows], 1),
        "outside_humidity": _avg([r["outside_humidity"] for r in rows], 1),
        "target_humidity": _avg([r["target_humidity"] for r in rows], 1),
        "time_in_range": _time_in_range(rows, band),
        "sample_count": len(rows),
    }


def _load_rows(start_timestamp: int, end_timestamp: int, grow_id: Optional[int]) -> List[dict]:
    ensure_schema()
    conn = _open(row_factory=True)
    try:
        query = f"""
            SELECT
                grow_id,
                timestamp,
                temperature,
                humidity,
                vpd,
                leaf_vpd,
                {OUTSIDE_TEMPERATURE_SQL} AS outside_temperature,
                {OUTSIDE_HUMIDITY_SQL} AS outside_humidity,
                target_humidity
            FROM sensor_data
            WHERE timestamp >= ? AND timestamp <= ?
        """ + AND_VALID_ROW
        params: List[object] = [start_timestamp, end_timestamp]
        if grow_id is not None:
            query += " AND grow_id = ?"
            params.append(grow_id)
        return [dict(row) for row in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def day_night_summaries(start_timestamp: int, end_timestamp: int, grow_id: Optional[int]) -> Dict:
    """
    `{"day": {...}, "night": {...}}` for the window. `grow_id=None` means every grow.

    Meant to be merged into a period summary that already has samples, so the
    caller decides what an empty window looks like.
    """
    rows = _load_rows(start_timestamp, end_timestamp, grow_id)

    by_grow: Dict[int, List[dict]] = {}
    for row in rows:
        by_grow.setdefault(row["grow_id"], []).append(row)

    day: List[dict] = []
    night: List[dict] = []
    for owner, group in by_grow.items():
        on, off = photoperiod_or_default(owner)
        part_day, part_night = split_rows(group, on, off, ARGENTINA_TZ)
        day.extend(part_day)
        night.extend(part_night)

    band = None
    if grow_id is not None:
        _, stage = _grow_identity(grow_id)
        band = grow_vpd_band(grow_id, stage)

    return {"day": _sub_summary(day, band), "night": _sub_summary(night, band)}
