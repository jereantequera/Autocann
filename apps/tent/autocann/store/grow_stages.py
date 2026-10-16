"""
Grow stages: read the copy of a profile's stages that a grow runs with.

The rows are written by `snapshot_profile_stages` (in create_grow, and by the
backfill at schema time); this module only reads them.
"""

from __future__ import annotations

from typing import Dict, List

from autocann.store.connection import _open
from autocann.store.schema import ensure_schema


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
