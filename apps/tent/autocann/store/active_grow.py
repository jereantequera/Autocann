"""
The active grow of each tent: which one is running, and switching it.

A tent has at most one active grow, enforced by the partial unique index
`idx_one_active_grow_per_tent` (see `_migrate_one_active_grow_per_tent`).
Every switch deactivates the tent's other grows before activating the new one,
inside one transaction, so the index is never violated along the way.
"""

from __future__ import annotations

from typing import Dict, Optional

from autocann.store.connection import _open
from autocann.store.grows import _grow_day_counters
from autocann.store.schema import ensure_schema


def get_active_grow(tent_id: int = 1) -> Optional[Dict]:
    """
    Get the active grow of a tent, with its day counters and its profile's name.

    `tent_id` defaults to the rig's only tent, so callers that do not name a
    tent get the same grow as before tents existed.

    Returns:
    - Dictionary with grow information or None. `profile_name` is None when the
      grow's profile no longer exists.
    """
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        try:
            row = conn.execute(
                """
                SELECT g.*, p.name AS profile_name
                FROM grows g
                LEFT JOIN crop_profiles p ON p.id = g.profile_id
                WHERE g.is_active = 1 AND g.tent_id = ?
                ORDER BY g.id DESC
                LIMIT 1
                """,
                (tent_id,),
            ).fetchone()
        finally:
            conn.close()

        if not row:
            return None

        grow = dict(row)
        grow.update(_grow_day_counters(grow))
        return grow
    except Exception as e:
        print(f"Error getting active grow: {e}")
        return None


def set_active_grow(grow_id: int) -> bool:
    """
    Make a grow the active one of its tent.

    Only the other grows of the same tent are deactivated; the rest of the
    tents keep their active grow. One transaction, deactivate before activate.

    Returns False, and writes nothing, when the grow does not exist.
    """
    try:
        ensure_schema()
        conn = _open()
        try:
            # Under the write lock, so the tent lookup and the updates see the same rows.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT tent_id FROM grows WHERE id = ?", (grow_id,)).fetchone()
            if row is None:
                conn.rollback()
                print(f"Error setting active grow: grow {grow_id} not found")
                return False
            tent_id = row[0]

            conn.execute(
                "UPDATE grows SET is_active = 0 WHERE tent_id = ? AND id != ?",
                (tent_id, grow_id),
            )
            conn.execute("UPDATE grows SET is_active = 1 WHERE id = ?", (grow_id,))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception as e:
        print(f"Error setting active grow: {e}")
        return False
