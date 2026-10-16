"""
Connection management: where the database is and how every connection is opened.
"""

from __future__ import annotations

import sqlite3

from autocann.paths import DB_PATH

#: Wait this long for a competing writer before raising "database is locked".
#: The control loop and the web app both write, so they do collide.
BUSY_TIMEOUT_SECONDS = 10.0


def _open(row_factory: bool = False) -> sqlite3.Connection:
    """
    Open a connection with the settings this workload needs.

    WAL lets the dashboard read while the control loop writes instead of both
    blocking each other, and busy_timeout replaces an immediate "database is
    locked" error with a short wait.
    """
    conn = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_SECONDS)
    if row_factory:
        conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_SECONDS * 1000)}")
    return conn
