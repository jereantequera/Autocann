"""
The health snapshot: every source read out of Redis and the database, then judged once.

This is the only place in the diagnostics package that does I/O for the health
answer. The verdicts it calls are pure and live in `health.py`.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from autocann.config import (
    INDOOR_SENSOR_MAX_AGE_SECONDS,
    MODE_ACTIVE,
    MODE_MONITOR,
    MODE_PAUSED,
    PRIMARY,
    TEST_MODE_KEY,
    TEST_MODE_SETTING,
    UNCONTROLLED_MODES,
    WITNESS,
    indoor_sensors_from_env,
)
from autocann.db import ALL_GROWS, get_active_grow, get_sensor_data_range, get_setting
from autocann.time import ARGENTINA_TZ

from .health import (
    ROLE_LABELS,
    _as_int,
    history_verdict,
    loop_verdict,
    overall_verdict,
    sensor_label,
    sensor_verdict,
)
from .nodes import NODE_INDEX_KEY, NODE_STATUS_PREFIX, node_report

# ---------------------------------------------------------------------------
# Reading it all out of Redis
# ---------------------------------------------------------------------------


def _json_get(redis_client, key):
    try:
        raw = redis_client.get(key)
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def control_mode(redis_client) -> Tuple[str, Optional[int]]:
    """
    Which of the three states the tent is in, and since when.

    Redis first because it is the fast path, the settings table second because
    it is the truth: a Redis that was restarted must not read as "not paused"
    and quietly hand a tent somebody is working inside back to the controller.

    A bare timestamp in Redis is the format from before monitoring existed, and
    the only safe reading of it is the one it always had: a pause.
    """
    try:
        raw = redis_client.get(TEST_MODE_KEY)
    except Exception:
        raw = None

    if raw:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        since = _as_int(raw)
        if since is not None:
            return MODE_PAUSED, since
        try:
            mirrored = json.loads(raw)
        except (TypeError, ValueError):
            mirrored = None
        if isinstance(mirrored, dict):
            mode = mirrored.get("mode")
            return (mode if mode in UNCONTROLLED_MODES else MODE_PAUSED), \
                _as_int(mirrored.get("since"), 0)

    stored = get_setting(TEST_MODE_SETTING, None)
    if isinstance(stored, dict) and stored.get("active"):
        mode = stored.get("mode")
        return (mode if mode in UNCONTROLLED_MODES else MODE_PAUSED), \
            _as_int(stored.get("since"), 0)
    return MODE_ACTIVE, None


def paused_since(redis_client) -> Optional[int]:
    """When the loop stopped being in charge, whichever way it stopped."""
    return control_mode(redis_client)[1]


def _last_stored_sample_age(now: int) -> Optional[float]:
    """
    How long ago the loop last wrote a row, for any grow.

    Deliberately ALL_GROWS: the question here is whether the installation is
    still recording, which is about the Raspberry and not about a crop. Scoped
    to the active grow, this would read as "the history stopped" during the
    minutes between ending one grow and starting the next, when nothing is
    wrong with anything.
    """
    try:
        rows = get_sensor_data_range(limit=1, grow_id=ALL_GROWS, valid_only=False)
    except Exception:
        return None
    if not rows:
        return None
    timestamp = _as_int(rows[0].get("timestamp"))
    return None if timestamp is None else max(0, now - timestamp)


def _has_active_grow() -> bool:
    """
    Whether there is a grow for the loop to write under.

    A database that cannot be read answers yes: the history check has its own
    way of saying nothing is being stored, and a "creá un cultivo" over an
    existing grow would send somebody to make a duplicate.
    """
    try:
        return get_active_grow() is not None
    except Exception:
        return True


def indoor_entries(redis_client, sensors, now: int) -> List[Dict]:
    """
    Every configured indoor sensor with its latest reading and its age.

    Driven by the configuration rather than by what happens to be in Redis: a
    sensor that has never posted has no key, and that is exactly the sensor
    somebody needs to see on the screen.
    """
    drift = (_json_get(redis_client, "sensor_status") or {}).get("drift") or {}
    flags = drift.get("flags") or {}

    entries = []
    for role, sensor_id, key in sensors.entries():
        payload = _json_get(redis_client, key)
        entry = {
            "role": role,
            "role_label": ROLE_LABELS.get(role, role),
            "sensor_id": sensor_id,
            "label": sensor_label(sensor_id),
            "redis_key": key,
            "controls": role == PRIMARY,
            "temperature": None,
            "humidity": None,
            "vpd": None,
            "timestamp": None,
            "datetime": None,
            "age_seconds": None,
            # Never-posted is not fresh. Defaulting the other way paints an
            # empty card green.
            "is_stale": True,
            "has_reading": False,
            "flags": list(flags.get(role) or []),
        }
        if payload:
            age = max(0, now - _as_int(payload.get("timestamp"), 0))
            entry.update({
                "temperature": payload.get("temperature"),
                "humidity": payload.get("humidity"),
                "vpd": payload.get("vpd"),
                "timestamp": payload.get("timestamp"),
                "datetime": payload.get("datetime"),
                "age_seconds": age,
                "is_stale": age > INDOOR_SENSOR_MAX_AGE_SECONDS,
                "has_reading": True,
            })
        entry.update(sensor_verdict(entry))
        entries.append(entry)

    return entries


def indoor_delta(entries: List[Dict]) -> Optional[Dict]:
    """
    Witness minus primary, the direction the drift monitor measures in.

    Only for two fresh readings: subtracting a stale number from a live one
    yields a difference that is about the clock rather than about the sensors,
    and on a dashboard that difference reads as a fault in the pair.
    """
    by_role = {entry["role"]: entry for entry in entries}
    primary, witness = by_role.get(PRIMARY), by_role.get(WITNESS)
    if primary is None or witness is None:
        return None
    if primary["is_stale"] or witness["is_stale"]:
        return None
    values = (primary["temperature"], witness["temperature"],
              primary["humidity"], witness["humidity"])
    if any(value is None for value in values):
        return None
    return {
        "temperature": round(witness["temperature"] - primary["temperature"], 2),
        "humidity": round(witness["humidity"] - primary["humidity"], 2),
    }


def node_names(redis_client) -> List[str]:
    index = _json_get(redis_client, NODE_INDEX_KEY)
    return [name for name in (index or []) if isinstance(name, str)]


# ---------------------------------------------------------------------------
# The snapshot
# ---------------------------------------------------------------------------


def health_snapshot(redis_client) -> Dict:
    """
    Everything the dashboard needs to answer "¿anda?", computed once.

    One answer rather than four endpoints, because the four could disagree —
    and did. Whatever is on the hero badge, on each sensor card and on the alert
    strip now comes from this same object, so they cannot contradict each other
    on screen again.
    """
    now = int(datetime.now(ARGENTINA_TZ).timestamp())

    try:
        sensors_config = indoor_sensors_from_env()
    except ValueError as e:
        return {
            "level": "error",
            "headline": "La configuración de sensores está mal",
            "detail": str(e),
            "sensors": [], "nodes": [], "delta": None, "drift": None,
            "outdoor": {"ok": None, "error": None},
            "has_witness": False, "max_age_seconds": INDOOR_SENSOR_MAX_AGE_SECONDS,
            "loop": loop_verdict(None, None, None, now),
            "history": history_verdict(None, "down"),
            "generated_at": now,
        }

    entries = indoor_entries(redis_client, sensors_config, now)

    # The outdoor DHT22 hangs off the Raspberry itself, so the control loop is
    # the only one who knows about it. It rides along here anyway: its badge is
    # on the same screen as the rest, and one more source for the dashboard to
    # poll is one more source that can disagree with this one.
    status = _json_get(redis_client, "sensor_status") or {}
    outdoor = status.get("outdoor") or {}

    published = _json_get(redis_client, "sensors")
    loop_age = None
    if published:
        loop_age = max(0, now - _as_int(published.get("timestamp"), 0))
    # The published mode is believed as well as the stored pause: it is what the
    # loop itself says it is doing, and it stays right across a rename of the
    # key the pause is stored under.
    mode, since = control_mode(redis_client)
    if since is None and published and str(published.get("mode") or "") in ("test", "paused"):
        mode = MODE_PAUSED
        since = _as_int(published.get("test_mode_since")
                        or published.get("test_mode_until")) or now
    elif since is None and published and str(published.get("mode") or "") == MODE_MONITOR:
        mode, since = MODE_MONITOR, now
    loop = loop_verdict(published, loop_age, since, now, mode=mode)

    # Asked here rather than left to /api/anomalies: the dashboard polls this
    # on every view, and the anomalies stop being fetched while dismissed, so a
    # notice that hangs off them would outlive the grow that answers it.
    has_grow = _has_active_grow()
    history = history_verdict(_last_stored_sample_age(now), loop["state"],
                              has_active_grow=has_grow)

    nodes = [node_report(_json_get(redis_client, f"{NODE_STATUS_PREFIX}:{name}"),
                         now, name=name)
             for name in node_names(redis_client)]

    snapshot = {
        "sensors": entries,
        "delta": indoor_delta(entries),
        "drift": status.get("drift"),
        "outdoor": {
            "ok": outdoor.get("ok"),
            "error": outdoor.get("error"),
        },
        "has_witness": sensors_config.has_witness,
        "max_age_seconds": INDOOR_SENSOR_MAX_AGE_SECONDS,
        "loop": loop,
        "history": history,
        "no_active_grow": not has_grow,
        "nodes": nodes,
        "generated_at": now,
    }
    snapshot.update(overall_verdict(entries, loop, history, nodes))
    return snapshot
