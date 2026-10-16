"""
Crop profiles: the stage templates a grow can follow.
"""

from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional

from autocann.store.connection import _open
from autocann.store.schema import ensure_schema


def get_profiles() -> List[Dict]:
    """Every crop profile, built-in first then by name. Each is `id`, `name`, `kind`."""
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        try:
            rows = conn.execute(
                "SELECT id, name, kind FROM crop_profiles ORDER BY kind = 'builtin' DESC, name ASC"
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]
    except sqlite3.Error as e:
        print(f"Error getting crop profiles: {e}")
        return []


def get_profile(profile_id: str) -> Optional[Dict]:
    """
    One profile with its `stages` in position order, or None when it does not exist.

    Each stage is a dict with every `profile_stages` column; `water_only` is a bool.
    """
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        try:
            profile = conn.execute(
                "SELECT id, name, kind FROM crop_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            if profile is None:
                return None
            stages = conn.execute(
                "SELECT profile_id, key, name, position, expected_days, vpd_min, vpd_max, "
                "rh_min, rh_max, water_only FROM profile_stages "
                "WHERE profile_id = ? ORDER BY position ASC",
                (profile_id,),
            ).fetchall()
        finally:
            conn.close()
        return {
            **dict(profile),
            "stages": [{**dict(row), "water_only": bool(row["water_only"])} for row in stages],
        }
    except sqlite3.Error as e:
        print(f"Error getting crop profile '{profile_id}': {e}")
        return None
