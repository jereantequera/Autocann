"""
Grows: create, end, and the stage history of each. The active grow of a tent is in `active_grow`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from autocann.store.connection import _open
from autocann.store.grow_stages import expected_days_by_stage, stage_table
from autocann.store.migrations import _parse_local_datetime, snapshot_profile_stages
from autocann.store.schema import ensure_schema
from autocann.store.seeds import BUILTIN_PROFILE_ID, BUILTIN_TENT_ID
from autocann.time import ARGENTINA_TZ

#: Ask for every grow at once, on purpose.
#:
#: `grow_id=None` means "the grow the dashboard is about", so without this there
#: was no way to spell "all of them" — and the only query that ever returned
#: every grow was the one that did it by accident, when no grow happened to be
#: active. Crossing ten months of empty tent with a real crop should take saying
#: so.
ALL_GROWS = "all"


#: Returned by `_resolve_grow_filter` when the caller asked for the active grow
#: and there is not one. Distinct from `None`, which there means "no filter".
_NO_GROW = object()


def _resolve_grow_filter(grow_id):
    """
    Which grow a query should filter on.

    Three answers, and the third is the point:

    - an **integer** — that grow;
    - **`ALL_GROWS`** — `None`, i.e. no `WHERE grow_id`;
    - **`None`** (the default) — the active grow, or `_NO_GROW` when there is
      none.

    That last case used to fall through to no filter at all, which is how the
    history silently became "every grow ever recorded" in exactly the window
    that matters: between ending one grow and starting the next. An empty
    answer is the honest one there — there is no active grow to report on — and
    it leaves "all of them" as something somebody chose rather than something
    that happened.
    """
    if isinstance(grow_id, str) and grow_id.strip().lower() == ALL_GROWS:
        return None
    if grow_id is not None:
        return int(grow_id)
    # Imported here: active_grow imports this module for the day counters.
    from autocann.store.active_grow import get_active_grow

    active_grow = get_active_grow()
    if active_grow:
        return int(active_grow["id"])
    return _NO_GROW


def _grow_day_counters(grow: Dict, now: Optional[int] = None) -> Dict:
    """
    Day-in-stage and day-of-grow for a grow row.

    Kept separate so a failure here degrades the counters to None instead of
    taking down get_active_grow(), which the control loop depends on.
    """
    from autocann.control.stages import build_timeline, current_period, day_number, estimated_end_timestamp

    empty = {
        "stage_started_at": None, "day_in_stage": None, "day_of_grow": None,
        "stage_expected_days": None, "stage_progress": None, "estimated_end": None,
    }
    try:
        if now is None:
            now = int(datetime.now(ARGENTINA_TZ).timestamp())

        grow_id = int(grow["id"])
        expected = expected_days_by_stage(stage_table(grow_id))
        periods = build_timeline(get_stage_events(grow_id), now=now, expected=expected)
        current = current_period(periods)
        started = _parse_local_datetime(grow.get("start_date"))

        return {
            "stage_started_at": current.started_at if current else None,
            "day_in_stage": current.days if current else None,
            "day_of_grow": day_number(started, now) if started else None,
            "stage_expected_days": current.expected_days if current else None,
            "stage_progress": current.progress if current else None,
            "estimated_end": estimated_end_timestamp(periods, now, expected),
        }
    except Exception as e:
        print(f"Error computing grow day counters: {e}")
        return empty


def create_grow(name: str, stage: str = "early_veg", notes: str = "",
                profile_id: str = BUILTIN_PROFILE_ID, tent_id: int = BUILTIN_TENT_ID) -> Optional[int]:
    """
    Create a grow in a tent, from a profile, and set it as active.

    The profile's stages are copied into `grow_stages` in the same transaction,
    so editing the profile later does not change this grow. Only the other
    active grows of the same tent are deactivated.

    Returns None, and writes nothing, when `stage` is not a stage of the profile
    or the profile or the tent does not exist.
    """
    try:
        current_time = datetime.now(ARGENTINA_TZ)
        started_at = int(current_time.timestamp())
        stamp = current_time.strftime("%Y-%m-%d %H:%M:%S")

        ensure_schema()
        conn = _open()
        try:
            # Under the write lock, so the checks and the inserts see the same rows.
            conn.execute("BEGIN IMMEDIATE")
            stage_keys = {row[0] for row in conn.execute(
                "SELECT key FROM profile_stages WHERE profile_id = ?", (profile_id,))}
            tent_exists = conn.execute("SELECT 1 FROM tents WHERE id = ?", (tent_id,)).fetchone()
            if stage not in stage_keys or tent_exists is None:
                conn.rollback()
                print(f"Error creating grow: stage '{stage}' is not in profile '{profile_id}', "
                      f"or tent {tent_id} does not exist")
                return None

            conn.execute("UPDATE grows SET is_active = 0 WHERE tent_id = ?", (tent_id,))
            cursor = conn.execute(
                """
                INSERT INTO grows (name, stage, start_date, is_active, notes, tent_id, profile_id)
                VALUES (?, ?, ?, 1, ?, ?, ?)
            """,
                (name, stage, stamp, notes, tent_id, profile_id),
            )
            grow_id = int(cursor.lastrowid)
            snapshot_profile_stages(conn, grow_id, profile_id)
            conn.execute(
                """
                INSERT INTO stage_events (grow_id, stage, started_at, datetime, notes)
                VALUES (?, ?, ?, ?, ?)
            """,
                (grow_id, stage, started_at, stamp, "Inicio del cultivo"),
            )
            conn.commit()
            return grow_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception as e:
        print(f"Error creating grow: {e}")
        return None


def end_grow(grow_id: int) -> bool:
    """
    End a grow by setting its end date and deactivating it.
    """
    try:
        current_time = datetime.now(ARGENTINA_TZ)

        ensure_schema()
        conn = _open()
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE grows
            SET end_date = ?, is_active = 0
            WHERE id = ?
        """,
            (current_time.strftime("%Y-%m-%d %H:%M:%S"), grow_id),
        )

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error ending grow: {e}")
        return False


def update_grow_stage(grow_id: int, stage: str, notes: Optional[str] = None) -> bool:
    """
    Move a grow to a stage and record the transition.

    Re-setting the stage it is already in is a no-op for the history: it would
    otherwise restart the day counter on an accidental double click.
    """
    try:
        ensure_schema()
        conn = _open()
        cursor = conn.cursor()
        row = cursor.execute("SELECT stage FROM grows WHERE id = ?", (grow_id,)).fetchone()
        if row is None:
            conn.close()
            print(f"Error updating grow stage: grow {grow_id} not found")
            return False
        previous = row[0]

        cursor.execute("UPDATE grows SET stage = ? WHERE id = ?", (stage, grow_id))
        conn.commit()
        conn.close()

        if previous != stage:
            record_stage_event(grow_id, stage, notes=notes)
        return True
    except Exception as e:
        print(f"Error updating grow stage: {e}")
        return False


def get_all_grows() -> List[Dict]:
    """
    Get all grows.
    """
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT g.*,
                   COUNT(s.id) as sample_count,
                   MIN(s.timestamp) as first_sample,
                   MAX(s.timestamp) as last_sample
            FROM grows g
            LEFT JOIN sensor_data s ON g.id = s.grow_id
            GROUP BY g.id
            ORDER BY g.id DESC
        """
        )

        rows = cursor.fetchall()
        conn.close()

        result = []
        for row in rows:
            grow_dict = dict(row)
            if grow_dict.get("first_sample"):
                grow_dict["first_sample_datetime"] = datetime.fromtimestamp(
                    grow_dict["first_sample"], ARGENTINA_TZ
                ).strftime("%Y-%m-%d %H:%M:%S")
            if grow_dict.get("last_sample"):
                grow_dict["last_sample_datetime"] = datetime.fromtimestamp(
                    grow_dict["last_sample"], ARGENTINA_TZ
                ).strftime("%Y-%m-%d %H:%M:%S")
            result.append(grow_dict)

        return result
    except Exception as e:
        print(f"Error getting all grows: {e}")
        return []


def get_stage_events(grow_id: int) -> List[Dict]:
    """Every stage change for a grow, oldest first."""
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        rows = conn.execute(
            """
            SELECT id, grow_id, stage, started_at, datetime, notes
            FROM stage_events
            WHERE grow_id = ?
            ORDER BY started_at ASC, id ASC
            """,
            (grow_id,),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Error getting stage events: {e}")
        return []


def record_stage_event(grow_id: int, stage: str, started_at: Optional[int] = None,
                       notes: Optional[str] = None) -> bool:
    """
    Record that a grow entered a stage.

    Called by create_grow() and update_grow_stage(); `grows.stage` is kept as a
    denormalised copy of the latest value so existing queries keep working.
    """
    try:
        current_time = datetime.now(ARGENTINA_TZ)
        if started_at is None:
            started_at = int(current_time.timestamp())
            stamp = current_time.strftime("%Y-%m-%d %H:%M:%S")
        else:
            stamp = datetime.fromtimestamp(started_at, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S")

        ensure_schema()
        conn = _open()
        conn.execute(
            """
            INSERT INTO stage_events (grow_id, stage, started_at, datetime, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (grow_id, stage, started_at, stamp, notes),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error recording stage event: {e}")
        return False


def get_stage_timeline(grow_id: int, now: Optional[int] = None) -> List[Dict]:
    """Stage periods with their durations, ready for the API."""
    from autocann.control.stages import build_timeline, timeline_to_dicts

    if now is None:
        now = int(datetime.now(ARGENTINA_TZ).timestamp())
    events = get_stage_events(grow_id)
    expected = expected_days_by_stage(stage_table(grow_id))
    return timeline_to_dicts(build_timeline(events, now=now, expected=expected))
