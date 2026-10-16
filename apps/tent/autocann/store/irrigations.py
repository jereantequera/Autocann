"""
Irrigations and the logbook built from them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from autocann.store.connection import _open
from autocann.store.schema import ensure_schema
from autocann.time import ARGENTINA_TZ


def record_irrigation(grow_id: int, litres: float, items: List[Dict],
                      date: Optional[str] = None, ph: Optional[float] = None,
                      ec: Optional[float] = None, stage: Optional[str] = None,
                      week: Optional[int] = None,
                      notes: str = "") -> Optional[int]:
    """
    Log one watering. Returns its id, or None if it could not be stored.

    `items` are the products actually poured, each `{name, unit, dose}` and
    optionally `product_id` and `on_plan`. An empty list is a plain-water
    watering, which is worth recording during a flush.
    """
    try:
        ensure_schema()
        now = datetime.now(ARGENTINA_TZ)
        if not date:
            date = now.strftime("%Y-%m-%d")

        conn = _open()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO irrigations
                        (grow_id, date, timestamp, litres, ph, ec, stage, week, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (grow_id, date, int(now.timestamp()), float(litres), ph, ec,
                     stage, week, notes or ""),
                )
                irrigation_id = cursor.lastrowid
                rows = []
                for item in items or []:
                    try:
                        dose = float(item.get("dose"))
                    except (TypeError, ValueError):
                        continue
                    if dose <= 0:
                        continue
                    rows.append((
                        irrigation_id,
                        item.get("product_id"),
                        str(item.get("name") or "").strip() or "Sin nombre",
                        item.get("unit") if item.get("unit") in ("ml/L", "g/L") else "ml/L",
                        dose,
                        1 if item.get("on_plan", True) else 0,
                    ))
                if rows:
                    conn.executemany(
                        """
                        INSERT INTO irrigation_items
                            (irrigation_id, product_id, name, unit, dose, on_plan)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
        finally:
            conn.close()
        return irrigation_id
    except Exception as e:
        print(f"Error recording irrigation: {e}")
        return None


def get_irrigations(grow_id: int, limit: int = 100) -> List[Dict]:
    """Waterings for a grow, most recent first, with what went into each."""
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        rows = conn.execute(
            """
            SELECT id, grow_id, date, timestamp, litres, ph, ec, stage, week, notes
            FROM irrigations
            WHERE grow_id = ?
            ORDER BY date DESC, id DESC
            LIMIT ?
            """,
            (grow_id, int(limit)),
        ).fetchall()

        irrigations = [dict(row) for row in rows]
        if irrigations:
            marks = ",".join("?" * len(irrigations))
            items = conn.execute(
                f"""
                SELECT irrigation_id, product_id, name, unit, dose, on_plan
                FROM irrigation_items
                WHERE irrigation_id IN ({marks})
                ORDER BY id ASC
                """,
                [i["id"] for i in irrigations],
            ).fetchall()
            by_irrigation: Dict[int, List[Dict]] = {}
            for item in items:
                entry = dict(item)
                entry["on_plan"] = bool(entry["on_plan"])
                by_irrigation.setdefault(entry.pop("irrigation_id"), []).append(entry)
            for irrigation in irrigations:
                irrigation["items"] = by_irrigation.get(irrigation["id"], [])
        conn.close()
        return irrigations
    except Exception as e:
        print(f"Error getting irrigations: {e}")
        return []


def get_logbook(since_ts: int, limit: int, grow_id: Optional[int] = None) -> List[Dict]:
    """
    Relay changes, waterings and stage changes since `since_ts`, newest first.

    Each entry carries a `source` ("control", "irrigation" or "stage") and the
    raw fields of that source; turning them into sentences is the route's job.

    Waterings and stage changes are scoped to `grow_id` (the active grow): last
    month's grow is not what today's log is about. Control events are global,
    because the relays do not know which grow is in the tent.

    `is_start` is computed over the whole grow, not the window, so a stage
    change inside the window still reads "Pasó a" when the grow began before it.
    """
    entries: List[Dict] = []
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        try:
            for row in conn.execute(
                """
                SELECT timestamp, event_type, value
                FROM control_events
                WHERE timestamp >= ?
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (int(since_ts), int(limit)),
            ).fetchall():
                entries.append({
                    "source": "control",
                    "timestamp": row["timestamp"],
                    "event_type": row["event_type"],
                    "value": row["value"],
                })

            if grow_id is not None:
                irrigation_rows = conn.execute(
                    """
                    SELECT id, timestamp, litres, ph, ec
                    FROM irrigations
                    WHERE grow_id = ? AND timestamp >= ?
                    ORDER BY timestamp DESC, id DESC
                    LIMIT ?
                    """,
                    (int(grow_id), int(since_ts), int(limit)),
                ).fetchall()
                products: Dict[int, List[Dict]] = {}
                if irrigation_rows:
                    marks = ",".join("?" * len(irrigation_rows))
                    for item in conn.execute(
                        f"""
                        SELECT irrigation_id, name, unit, dose
                        FROM irrigation_items
                        WHERE irrigation_id IN ({marks})
                        ORDER BY id ASC
                        """,
                        [r["id"] for r in irrigation_rows],
                    ).fetchall():
                        products.setdefault(item["irrigation_id"], []).append(
                            {"name": item["name"], "unit": item["unit"], "dose": item["dose"]}
                        )
                for row in irrigation_rows:
                    entries.append({
                        "source": "irrigation",
                        "timestamp": row["timestamp"],
                        "litres": row["litres"],
                        "ph": row["ph"],
                        "ec": row["ec"],
                        "items": products.get(row["id"], []),
                    })

                stage_rows = conn.execute(
                    """
                    SELECT id, stage, started_at
                    FROM stage_events
                    WHERE grow_id = ?
                    ORDER BY started_at ASC, id ASC
                    """,
                    (int(grow_id),),
                ).fetchall()
                first_id = stage_rows[0]["id"] if stage_rows else None
                for row in stage_rows:
                    if row["started_at"] < since_ts:
                        continue
                    entries.append({
                        "source": "stage",
                        "timestamp": row["started_at"],
                        "stage": row["stage"],
                        "is_start": row["id"] == first_id,
                    })
        finally:
            conn.close()
    except Exception as e:
        print(f"Error getting logbook: {e}")
        return []

    entries.sort(key=lambda entry: entry["timestamp"], reverse=True)
    return entries[: int(limit)]


def delete_irrigation(irrigation_id: int) -> bool:
    """Remove a watering and everything that was in it."""
    try:
        ensure_schema()
        conn = _open()
        try:
            with conn:
                conn.execute(
                    "DELETE FROM irrigation_items WHERE irrigation_id = ?", (irrigation_id,)
                )
                deleted = conn.execute(
                    "DELETE FROM irrigations WHERE id = ?", (irrigation_id,)
                ).rowcount
        finally:
            conn.close()
        return deleted > 0
    except Exception as e:
        print(f"Error deleting irrigation: {e}")
        return False


def last_irrigation_litres(grow_id: int, default: float = 10.0) -> float:
    """
    How many litres were mixed last time, to pre-fill the next one.

    Saves re-entering the same volume on every watering without needing a
    settings table for it.
    """
    try:
        ensure_schema()
        conn = _open()
        row = conn.execute(
            "SELECT litres FROM irrigations WHERE grow_id = ? ORDER BY id DESC LIMIT 1",
            (grow_id,),
        ).fetchone()
        conn.close()
        return float(row[0]) if row and row[0] else default
    except Exception as e:
        print(f"Error reading last irrigation volume: {e}")
        return default
