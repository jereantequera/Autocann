"""
Wall-clock time in the tent's zone, for timestamps only. Control decisions never gate on it:
see the note on the monotonic clock in `autocann.cli.vpd`.
"""

from __future__ import annotations

from datetime import datetime

from autocann.time import ARGENTINA_TZ


def _now_local() -> datetime:
    return datetime.now(ARGENTINA_TZ)
