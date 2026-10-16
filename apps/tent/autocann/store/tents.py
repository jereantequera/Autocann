"""
Tents: the grow spaces. The rig has one, seeded at startup; grows belong to one.
"""

from __future__ import annotations

from typing import Dict, Optional

from autocann.store.connection import _open
from autocann.store.schema import ensure_schema


def get_tent(tent_id: int) -> Optional[Dict]:
    """One tent as `id`, `name`, `created_at`, or None when it does not exist."""
    ensure_schema()
    conn = _open(row_factory=True)
    try:
        row = conn.execute(
            "SELECT id, name, created_at FROM tents WHERE id = ?", (tent_id,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def rename_tent(tent_id: int, name: str) -> bool:
    """
    Give a tent a new name. False when the name is blank or the tent does not exist.
    """
    name = name.strip()
    if not name:
        return False
    ensure_schema()
    conn = _open()
    try:
        cursor = conn.execute("UPDATE tents SET name = ? WHERE id = ?", (name, tent_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
