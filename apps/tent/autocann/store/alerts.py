"""
Alert state: which alerts are showing, and which the grower has seen or snoozed.

The detectors (`control/anomalies.py`, re-read by `web/alert_owners.py`) are
pure and say what is wrong *now*; they remember nothing. This is the memory, so
that "Visto" holds across reloads and devices.

Identity. An alert is `(scope, type)` while its condition lasts, and the
detector's `last_at` (the newest occurrence, as an epoch) says whether anything
new happened under it:

- Same key, same `last_at`: the same alert. An acknowledgement holds.
- Same key, newer `last_at`: a new occurrence of an open alert (a second gap in
  the night). It is unread again: the grower saw the first one, not this one.
- The key gone from the detector's answer: resolved. If it comes back with a
  new `last_at`, it is a new alert, unread.
- The key back with the *same* `last_at` it was resolved with: the same alert
  that was only hidden for a while (the loop paused, or the red banner took it
  over). Reopened, acknowledgement and all, rather than shown again as new.

A snooze is about the grower's time, not about the alert: a new occurrence does
not cancel it. It ends when it ends.
"""

from __future__ import annotations

import sqlite3
from typing import Dict, Iterable, List, Optional

from autocann.store.connection import _open
from autocann.store.schema import ensure_schema

#: The longest a snooze may last: a week. Longer than that is "never", and
#: "never" is a decision about the alert itself, not something to snooze.
MAX_SNOOZE_MINUTES = 7 * 24 * 60


def alert_key(scope, kind: str) -> str:
    """The identity of an alert while its condition lasts."""
    return f"{scope}:{kind}"


def is_unread(row: Dict, now: int) -> bool:
    """Whether the dashboard should still be showing this alert as news."""
    if row.get("acknowledged_at") is not None:
        return False
    snoozed = row.get("snoozed_until")
    return snoozed is None or snoozed <= now


def _occurrence(item: Dict) -> Optional[int]:
    value = item.get("last_at")
    return int(value) if isinstance(value, (int, float)) else None


def sync_alerts(items: Iterable[Dict], scope, now: int) -> List[Dict]:
    """
    Record what the detector says now, and return it with each alert's state.

    Every returned item is the detector's own, plus `id`, `acknowledged_at`,
    `snoozed_until` and `unread`. Open alerts the detector no longer reports are
    marked resolved. One transaction, taken for writing up front, so two
    dashboards polling at once cannot both open the same alert.
    """
    items = [i for i in items if isinstance(i, dict) and i.get("type")]
    ensure_schema()
    conn = _open(row_factory=True)
    try:
        conn.execute("BEGIN IMMEDIATE")
        prefix = f"{scope}:"
        open_rows = {
            row["key"]: dict(row)
            for row in conn.execute(
                "SELECT * FROM notifications WHERE resolved_at IS NULL AND key LIKE ? || '%'",
                (prefix,),
            )
        }
        out: List[Dict] = []
        seen = set()
        for item in items:
            key = alert_key(scope, item["type"])
            if key in seen:
                continue  # one item per type; a second one would be a detector bug
            seen.add(key)
            occurrence = _occurrence(item)
            row = open_rows.get(key) or _reopen(conn, key, occurrence)
            if row is None:
                started = item.get("first_at")
                started = int(started) if isinstance(started, (int, float)) else now
                cursor = conn.execute(
                    "INSERT INTO notifications (key, type, severity, message, started_at, "
                    "last_event_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (key, item["type"], item.get("severity") or "warning",
                     item.get("message") or "", started, occurrence, now),
                )
                row = {"id": cursor.lastrowid, "acknowledged_at": None, "snoozed_until": None}
            else:
                newer = occurrence is not None and (
                    row.get("last_event_at") is None or occurrence > row["last_event_at"])
                if newer:
                    row["acknowledged_at"] = None
                conn.execute(
                    "UPDATE notifications SET severity = ?, message = ?, "
                    "last_event_at = COALESCE(?, last_event_at), acknowledged_at = ? "
                    "WHERE id = ?",
                    (item.get("severity") or "warning", item.get("message") or "",
                     occurrence, row["acknowledged_at"], row["id"]),
                )
            state = {"id": row["id"], "acknowledged_at": row["acknowledged_at"],
                     "snoozed_until": row["snoozed_until"]}
            out.append({**item, **state, "unread": is_unread(state, now)})

        gone = [row["id"] for key, row in open_rows.items() if key not in seen]
        if gone:
            conn.executemany("UPDATE notifications SET resolved_at = ? WHERE id = ?",
                             [(now, row_id) for row_id in gone])
        conn.commit()
        return out
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def _reopen(conn: sqlite3.Connection, key: str, occurrence: Optional[int]) -> Optional[Dict]:
    """The resolved alert that is back with the same occurrence, reopened; or None."""
    if occurrence is None:
        return None
    row = conn.execute(
        "SELECT * FROM notifications WHERE key = ? AND resolved_at IS NOT NULL "
        "AND last_event_at = ? ORDER BY id DESC LIMIT 1",
        (key, occurrence),
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE notifications SET resolved_at = NULL WHERE id = ?", (row["id"],))
    return dict(row)


def _set(alert_id: int, column: str, value: int) -> Optional[Dict]:
    ensure_schema()
    conn = _open(row_factory=True)
    try:
        cursor = conn.execute(
            f"UPDATE notifications SET {column} = ? WHERE id = ? AND resolved_at IS NULL",
            (value, alert_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM notifications WHERE id = ?", (alert_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def acknowledge_alert(alert_id: int, now: int) -> Optional[Dict]:
    """Mark an open alert as seen. None when there is no such open alert."""
    return _set(alert_id, "acknowledged_at", now)


def snooze_alert(alert_id: int, until: int) -> Optional[Dict]:
    """Hide an open alert until `until` (epoch). None when there is no such open alert."""
    return _set(alert_id, "snoozed_until", until)
