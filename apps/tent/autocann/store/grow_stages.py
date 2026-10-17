"""
Grow stages: read the copy of a profile's stages that a grow runs with.

The rows are written by `snapshot_profile_stages` (in create_grow, and by the
backfill at schema time); this module only reads them.

The stage tables below (`key -> stage row`, in position order) are what the
dashboard, the API and the analytics read instead of the constants in
`control/vpd_math.py`. Without an active grow, or for a grow with no rows, the
built-in profile answers, which is what the tent ran with before profiles.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from autocann.store.connection import _open
from autocann.store.profiles import get_profile
from autocann.store.schema import ensure_schema
from autocann.store.seeds import BUILTIN_PROFILE_ID

#: VPD band used by the score when a grow's stage has no VPD band of its own
#: (a humidity-only stage). It is the band the scores always used for an
#: unknown stage.
DEFAULT_VPD_BAND: Tuple[float, float] = (0.6, 1.5)


def get_grow_stages(grow_id: int) -> List[Dict]:
    """
    A grow's own stages in position order, each with every `grow_stages` column.

    `water_only` is a bool. An unknown grow has no stages, so the list is empty.
    """
    ensure_schema()
    conn = _open(row_factory=True)
    try:
        rows = conn.execute(
            "SELECT grow_id, key, name, position, expected_days, vpd_min, vpd_max, "
            "rh_min, rh_max, water_only FROM grow_stages "
            "WHERE grow_id = ? ORDER BY position ASC",
            (grow_id,),
        ).fetchall()
    finally:
        conn.close()
    return [{**dict(row), "water_only": bool(row["water_only"])} for row in rows]


def _by_key(stages: List[Dict]) -> Dict[str, Dict]:
    return {stage["key"]: stage for stage in stages}


def profile_stage_table(profile_id: str) -> Dict[str, Dict]:
    """The stages of a profile as `key -> stage`, in position order. Empty if unknown."""
    profile = get_profile(profile_id)
    return _by_key(profile["stages"]) if profile else {}


def builtin_stage_table() -> Dict[str, Dict]:
    """The built-in profile's stages: what the tent ran with before profiles existed."""
    return profile_stage_table(BUILTIN_PROFILE_ID)


def stage_table(grow_id: int) -> Dict[str, Dict]:
    """
    A grow's stages as `key -> stage`, in position order.

    A grow with no stage rows (an id that does not exist) is judged against the
    built-in profile, so callers never get an empty table for a valid request.
    """
    stages = get_grow_stages(grow_id)
    return _by_key(stages) if stages else builtin_stage_table()


def _active_grow_id() -> Optional[int]:
    """The active grow's id, or None. Same rule as `get_active_grow`, without its day counters."""
    ensure_schema()
    conn = _open()
    try:
        row = conn.execute(
            "SELECT id FROM grows WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row else None


def active_stage_table() -> Dict[str, Dict]:
    """The active grow's stages as `key -> stage`; the built-in profile's when there is no active grow."""
    grow_id = _active_grow_id()
    return stage_table(grow_id) if grow_id is not None else builtin_stage_table()


def expected_days_by_stage(table: Dict[str, Dict]) -> Dict[str, Optional[int]]:
    """`key -> expected_days` for the timeline and feed functions. None means open-ended."""
    return {key: stage["expected_days"] for key, stage in table.items()}


def grow_vpd_band(grow_id: int, stage: str) -> Tuple[float, float]:
    """The VPD band a grow's stage is held to; the default when the stage is unknown or has none."""
    row = stage_table(grow_id).get(stage)
    if row is None or row["vpd_min"] is None or row["vpd_max"] is None:
        return DEFAULT_VPD_BAND
    return (row["vpd_min"], row["vpd_max"])
