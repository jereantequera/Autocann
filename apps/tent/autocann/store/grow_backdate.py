"""
Backdating a grow: the simulator's history starts a grow in the past.

Kept out of `grows.py`, which is at its size limit. Live stage changes never
come through here; they go through `update_grow_stage`.
"""

from __future__ import annotations

from datetime import datetime

from autocann.store.connection import _open
from autocann.store.schema import ensure_schema
from autocann.time import ARGENTINA_TZ


def backdate_grow(grow_id: int, started_at: int, stage: str) -> bool:
    """
    Start a grow at `started_at`, with `stage` as its current stage.

    Moves the start date and the first stage event (the one recorded when the
    grow was created) to `started_at`, and sets `grows.stage` without recording
    a change. Only the simulator's history uses it.
    """
    try:
        stamp = datetime.fromtimestamp(started_at, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
        ensure_schema()
        conn = _open()
        with conn:
            conn.execute("UPDATE grows SET start_date = ?, stage = ? WHERE id = ?",
                         (stamp, stage, grow_id))
            conn.execute(
                """
                UPDATE stage_events SET started_at = ?, datetime = ?
                WHERE id = (SELECT id FROM stage_events WHERE grow_id = ?
                            ORDER BY started_at ASC, id ASC LIMIT 1)
                """,
                (started_at, stamp, grow_id),
            )
        conn.close()
        return True
    except Exception as e:
        print(f"Error backdating grow: {e}")
        return False
