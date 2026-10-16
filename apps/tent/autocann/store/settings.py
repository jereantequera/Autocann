"""
Key/value settings stored in the database.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from autocann.store.connection import _open
from autocann.store.schema import ensure_schema
from autocann.time import ARGENTINA_TZ

# ===============================
# Settings
# ===============================


def get_setting(key: str, default=None):
    """
    One stored setting, already parsed. `default` when it is absent or corrupt.

    Corrupt reads as absent on purpose: this is read by the control loop, and a
    bad row must not stop the tent from being controlled.
    """
    ensure_schema()
    try:
        conn = _open()
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return default
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return default


def set_setting(key: str, value) -> bool:
    """Store one setting. Returns whether it was written."""
    ensure_schema()
    try:
        conn = _open()
        try:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, json.dumps(value),
                 datetime.now(ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except sqlite3.Error as e:
        print(f"❌ No se pudo guardar la configuración '{key}': {e}")
        return False
