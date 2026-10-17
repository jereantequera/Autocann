"""
Test mode: the operator's pause of the control loop, and the relays moved by hand
while it is paused.
"""

from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, jsonify, request

from autocann.config import (
    CONTROL_MODES,
    MODE_ACTIVE,
    MODE_PAUSED,
    TEST_MODE_KEY,
    TEST_MODE_OUTPUTS_KEY,
    TEST_MODE_SETTING,
    UNCONTROLLED_MODES,
)
from autocann.db import (
    get_setting,
    set_setting,
)
from autocann.hardware.outputs import (
    find_output,
    output_names,
)
from autocann.time import ARGENTINA_TZ
from autocann.web.deps import get_redis

bp = Blueprint("test_mode", __name__)


def control_mode():
    """
    Which of the three states the tent is in, as (mode, since).

    The database is the truth and Redis is the mirror, so a Redis that was
    restarted or flushed does not read as "not paused" and quietly hand the
    tent back to the controller. Redis is still answered from first because
    it is the common case and costs nothing.

    A bare timestamp in Redis predates monitoring, and the only safe
    reading of it is the one it always had: a pause.
    """
    raw = None
    try:
        raw = get_redis().get(TEST_MODE_KEY)
    except Exception:
        raw = None

    if raw:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            return MODE_PAUSED, int(raw)
        except (TypeError, ValueError):
            pass
        try:
            mirrored = json.loads(raw)
        except (TypeError, ValueError):
            mirrored = None
        if isinstance(mirrored, dict):
            mode = mirrored.get("mode")
            if mode not in UNCONTROLLED_MODES:
                mode = MODE_PAUSED
            try:
                return mode, int(mirrored.get("since"))
            except (TypeError, ValueError):
                return mode, int(datetime.now(ARGENTINA_TZ).timestamp())

    stored = get_setting(TEST_MODE_SETTING, None)
    if not isinstance(stored, dict) or not stored.get("active"):
        return MODE_ACTIVE, None

    mode = stored.get("mode")
    if mode not in UNCONTROLLED_MODES:
        mode = MODE_PAUSED

    since = stored.get("since")
    try:
        since = int(since)
    except (TypeError, ValueError):
        since = int(datetime.now(ARGENTINA_TZ).timestamp())

    # Seen in the database but not in Redis: put the mirror back, so the
    # control loop - which only reads Redis each cycle - sees it too.
    try:
        get_redis().set(TEST_MODE_KEY, json.dumps({"mode": mode, "since": since}))
    except Exception:
        pass
    return mode, since

def test_mode_payload():
    mode, since = control_mode()
    try:
        raw = get_redis().get(TEST_MODE_OUTPUTS_KEY)
        requested = json.loads(raw) if raw else {}
    except Exception:
        requested = {}
    if not isinstance(requested, dict):
        requested = {}

    return {
        # Kept, and kept meaning the same thing: "the loop is not in
        # charge". Every caller that only ever asked that question - the
        # relay buttons, the chip's disabled state - keeps working without
        # learning about the third state.
        "active": since is not None,
        "mode": mode,
        "since": since,
        # How long it has been, not how long is left: there is no deadline
        # to count down to any more, and the number that matters is how long
        # the tent has been going unattended.
        "elapsed_seconds": (
            None if since is None
            else max(0, int(datetime.now(ARGENTINA_TZ).timestamp()) - since)
        ),
        "requested_outputs": {
            name: bool(requested.get(name, False)) for name in output_names()
        },
    }


# ===============================
# Test mode
# ===============================

@bp.route("/api/test-mode", methods=["GET"])
def get_test_mode():
    """
    Whether the control loop is standing down, and since when.
    """
    try:
        return jsonify(test_mode_payload())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/test-mode", methods=["POST"])
def set_test_mode():
    """
    Put the tent in one of its three states.

    Body (JSON), either:
    - mode: "active" | "monitor" | "paused"
    - enabled: boolean  (the older spelling; true means "paused")

    `enabled` is still accepted because it is what every build before
    monitoring sent, and a dashboard cached in a browser on the tent wall
    goes on sending it for as long as that tab stays open.

    The three states exist because "no decidas" and "no guardes" turned out
    not to be the same request. Pausing is for working on the installation:
    nothing decides, nothing is recorded, and the relays follow what is
    asked by hand. Monitoring is for a tent that is set up but not planted:
    nothing decides and nothing switches, and every reading is recorded,
    because what the room does on its own is the baseline worth having
    before there is anything alive in there to protect.

    Neither ends on its own. The pause used to expire after eight hours;
    what that bought - never being left uncontrolled by accident - is now
    bought by saying so in the header on every view instead, and what it
    cost was relays closing again at a moment nobody chose.

    Written to the database first, then mirrored into Redis. The loop reads
    Redis, so the order matters only when one of the two writes fails: a
    state recorded and not mirrored is noticed and repaired on the next
    read, while one mirrored and not recorded would vanish on the next
    reboot with nothing to notice it.
    """
    data = request.get_json(silent=True) or {}

    mode = data.get("mode")
    if mode is None:
        enabled = data.get("enabled")
        if not isinstance(enabled, bool):
            return jsonify({
                "error": "Missing or invalid 'mode' "
                         f"(one of: {', '.join(CONTROL_MODES)})"
            }), 400
        mode = MODE_PAUSED if enabled else MODE_ACTIVE
    if mode not in CONTROL_MODES:
        return jsonify({
            "error": f"Unknown mode '{mode}'. Valid modes: {', '.join(CONTROL_MODES)}"
        }), 400

    if mode == MODE_ACTIVE:
        if not set_setting(TEST_MODE_SETTING, {"active": False, "mode": MODE_ACTIVE}):
            return jsonify({"error": "No se pudo devolver el control al lazo"}), 500
        try:
            get_redis().delete(TEST_MODE_KEY)
            # Cleared together: a later pause must not begin by energising
            # whatever was left switched on in this one.
            get_redis().delete(TEST_MODE_OUTPUTS_KEY)
        except Exception as e:
            return jsonify({"error": f"Failed to leave test mode: {e}"}), 500
        return jsonify({"success": True, **test_mode_payload()})

    # Switching between the two stopped states keeps the original instant:
    # the tent has been uncontrolled since then either way, and restarting
    # the count would make a stop somebody forgot about look fresh.
    _, current_since = control_mode()
    since = current_since if current_since is not None \
        else int(datetime.now(ARGENTINA_TZ).timestamp())

    if not set_setting(TEST_MODE_SETTING,
                       {"active": True, "since": since, "mode": mode}):
        return jsonify({"error": "No se pudo registrar el estado"}), 500
    try:
        get_redis().set(TEST_MODE_KEY, json.dumps({"mode": mode, "since": since}))
        if mode == MODE_PAUSED:
            # Everything starts off. Pausing is not a reason for a relay to
            # close.
            get_redis().set(
                TEST_MODE_OUTPUTS_KEY,
                json.dumps({name: False for name in output_names()}),
            )
        else:
            # Monitoring switches nothing, so a request left over from an
            # earlier pause must not survive into it.
            get_redis().delete(TEST_MODE_OUTPUTS_KEY)
    except Exception as e:
        return jsonify({"error": f"Failed to enter test mode: {e}"}), 500

    return jsonify({"success": True, **test_mode_payload()})

@bp.route("/api/test-mode/outputs", methods=["POST"])
def set_test_mode_output():
    """
    Ask an output to switch while the loop is paused.

    Refused when it is not: the control loop would ignore it, and a request
    that silently does nothing is worse than one that fails.

    The request is not a guarantee. The loop still honours the minimum
    on/off times — they protect the equipment, which does not care that a
    person asked — so an output may stay put for a few seconds. The loop
    reports what it held back.
    """
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    state = data.get("state")

    if not name or not isinstance(name, str):
        return jsonify({"error": "Missing or invalid 'name'"}), 400
    if not isinstance(state, bool):
        return jsonify({"error": "Missing or invalid 'state' (must be boolean)"}), 400
    if not find_output(name):
        valid = ", ".join(output_names())
        return jsonify({"error": f"Unknown output name '{name}'. Valid names: {valid}"}), 404

    # Only the pause. Monitoring switching a relay by hand would be the one
    # thing that makes its history worthless: the whole value of those rows
    # is that they record a room with nothing running in it.
    if control_mode()[0] != MODE_PAUSED:
        return jsonify({
            "error": "Los relés sólo se mueven a mano con el sistema en pausa. "
                     "En monitoreo nada se enciende, a propósito."
        }), 409

    try:
        raw = get_redis().get(TEST_MODE_OUTPUTS_KEY)
        requested = json.loads(raw) if raw else {}
        if not isinstance(requested, dict):
            requested = {}
        requested[name] = state
        # No TTL: these belong to the pause, and the pause is ended by
        # someone rather than by a clock. Leaving the pause deletes them.
        get_redis().set(TEST_MODE_OUTPUTS_KEY, json.dumps(requested))
    except Exception as e:
        return jsonify({"error": f"Failed to publish the request: {e}"}), 500

    return jsonify({"success": True, **test_mode_payload()})
