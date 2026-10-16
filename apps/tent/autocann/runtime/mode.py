"""
Which of the three states the tent is in (control, monitoring, pause): read from the Redis
mirror every cycle, restored from the database when the mirror is empty.
"""

from __future__ import annotations

import json
from typing import Optional, Tuple

from autocann.config import (
    MODE_ACTIVE,
    MODE_MONITOR,
    MODE_PAUSED,
    TEST_MODE_KEY,
    TEST_MODE_SETTING,
    UNCONTROLLED_MODES,
)
from autocann.db import get_setting
from autocann.runtime.clock import _now_local
from autocann.runtime.redis import redis_client


def read_test_mode_since() -> Optional[int]:
    """
    When the pause started, or None when the loop is in control.

    Read from Redis every cycle because it is cheap. Redis is a mirror, not the
    truth - `restore_pause_mirror` puts it back from the database - so the one
    thing this cannot do is treat a missing key as "resume". It still treats an
    *unreachable* Redis as resume, which is a different case: the loop cannot
    be driven by hand without it either, so carrying on controlling the tent is
    the useful answer rather than standing down with nobody able to intervene.
    """
    mode, since = read_control_mode()
    return since if mode in UNCONTROLLED_MODES else None


def parse_mode_mirror(raw) -> Tuple[str, Optional[int]]:
    """
    The Redis mirror, as (mode, since).

    Two formats, on purpose. It now holds `{"mode": ..., "since": ...}`, but a
    Redis that survived the deploy which added monitoring still holds a bare
    timestamp from before - and the one reading of that which is safe is the
    one it always had: a pause. Guessing "monitor" would hand the relays to a
    controller somebody had deliberately stopped.
    """
    if not raw:
        return MODE_ACTIVE, None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")

    text = str(raw).strip()
    try:
        return MODE_PAUSED, int(text)
    except (TypeError, ValueError):
        pass

    try:
        stored = json.loads(text)
    except (TypeError, ValueError):
        return MODE_ACTIVE, None
    if not isinstance(stored, dict):
        return MODE_ACTIVE, None

    mode = stored.get("mode")
    if mode not in UNCONTROLLED_MODES:
        # A mode this build does not know is not a reason to start driving a
        # tent somebody stopped. Stand down and keep the history.
        mode = MODE_MONITOR if mode else MODE_PAUSED
    try:
        since = int(stored.get("since"))
    except (TypeError, ValueError):
        since = None
    return mode, since


def read_control_mode() -> Tuple[str, Optional[int]]:
    """
    Which of the three states the tent is in, and since when.

    Read from Redis every cycle because it is cheap. Redis is a mirror, not the
    truth - `restore_pause_mirror` puts it back from the database - so the one
    thing this cannot do is treat a missing key as "resume". It still treats an
    *unreachable* Redis as resume, which is a different case: the loop cannot
    be driven by hand without it either, so carrying on controlling the tent is
    the useful answer rather than standing down with nobody able to intervene.
    """
    try:
        raw = redis_client.get(TEST_MODE_KEY)
    except Exception:
        return MODE_ACTIVE, None
    return parse_mode_mirror(raw)


def stored_control_mode() -> Tuple[str, Optional[int]]:
    """
    What the database says, which is the truth.

    A record written before monitoring existed carries no `mode`, and every one
    of those meant a pause.
    """
    stored = get_setting(TEST_MODE_SETTING, None)
    if not isinstance(stored, dict) or not stored.get("active"):
        return MODE_ACTIVE, None

    mode = stored.get("mode")
    if mode not in UNCONTROLLED_MODES:
        mode = MODE_PAUSED
    try:
        since = int(stored.get("since"))
    except (TypeError, ValueError):
        since = None
    return mode, since


def restore_pause_mirror() -> None:
    """
    Put the Redis mirror back when the database says the tent is not controlled.

    Redis is memory. Restarting it, or the Raspberry, used to mean a pause
    silently ended and the relays started moving again at a moment nobody
    chose. Checked on the settings interval rather than every cycle: it only
    has to beat somebody walking back to the tent.
    """
    mode, since = stored_control_mode()
    if mode not in UNCONTROLLED_MODES:
        return
    try:
        if redis_client.get(TEST_MODE_KEY):
            return
        since = int(since or int(_now_local().timestamp()))
        redis_client.set(TEST_MODE_KEY, json.dumps({"mode": mode, "since": since}))
        label = "Monitoreo" if mode == MODE_MONITOR else "Pausa"
        print(f"⏸️ {label} restaurado desde la base: Redis no lo tenía")
    except Exception:
        pass
