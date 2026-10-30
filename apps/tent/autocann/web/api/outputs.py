"""
Relays: their state, manual overrides, what the node should switch, and quiet hours.
"""

from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, jsonify, request

from autocann.config import (
    DESIRED_OUTPUTS_KEY,
    DESIRED_OUTPUTS_MAX_AGE_SECONDS,
)
from autocann.control.quiet import ACTION_OUTPUTS, QuietWindow, parse_hhmm, windows_from_dict
from autocann.db import (
    get_setting,
    set_setting,
)
from autocann.hardware import ir_remote
from autocann.hardware.outputs import (
    find_output,
    get_outputs,
    manual_override_key,
    output_names,
    parse_bool_flag,
    pin_sources,
)
from autocann.time import ARGENTINA_TZ
from autocann.web.api.test_mode import test_mode_payload
from autocann.web.deps import get_redis
from autocann.web.errors import error_response

bp = Blueprint("outputs", __name__)


#: Default and maximum lifetime of a manual output override.
MANUAL_OVERRIDE_DEFAULT_SECONDS = 15 * 60
MANUAL_OVERRIDE_MAX_SECONDS = 2 * 60 * 60


@bp.route("/api/output-status", methods=["GET"])
def get_output_status():
    """
    Endpoint to get current output/relay status from Redis plus, per output,
    the board and pin that actually switch it.

    Carries the test-mode state as well. The dashboard draws both from this
    one answer so they cannot disagree: two polls could show manual buttons
    over outputs the loop is already deciding again, which is the one
    confusion worth designing out.
    """
    # Published by the control loop: what it refused to switch yet, and for
    # how long, so a relay that does not move can say why.
    held = {}
    try:
        raw = get_redis().get("sensors")
        if raw:
            held = (json.loads(raw) or {}).get("held_outputs") or {}
    except Exception:
        held = {}

    # Resolved once, not per output: it reads the environment.
    sources = pin_sources()

    outputs = []
    for o in get_outputs():
        redis_key = o.get("redis_key")
        state = parse_bool_flag(get_redis().get(redis_key) if redis_key else None)

        override_raw = get_redis().get(manual_override_key(o.get("name", "")))
        outputs.append(
            {
                "name": o.get("name"),
                "label": o.get("label"),
                "pin": sources.get(o.get("name")),
                "redis_key": redis_key,
                "state": state,
                "manual_override": parse_bool_flag(override_raw),
                "held_seconds": held.get(o.get("name")),
            }
        )

    try:
        test_mode = test_mode_payload()
    except Exception:
        test_mode = {"active": False}

    return jsonify({"outputs": outputs, "test_mode": test_mode})


@bp.route("/api/output-control", methods=["POST"])
def set_output_control():
    """
    Request a manual override of an output.

    Body (JSON):
    - name: output name (see autocann.hardware.outputs.get_outputs())
    - state: boolean (true=on, false=off), or null to hand the output back
      to automatic control
    - duration_seconds: optional, how long the override lasts
      (default MANUAL_OVERRIDE_DEFAULT_SECONDS, max MANUAL_OVERRIDE_MAX_SECONDS)

    The override is published to Redis; the control loop applies it. This
    process deliberately does not touch the GPIO: only one process may own a
    pin, and the previous implementation opened a device and closed it again
    straight away, which released the pin and dropped the relay the moment
    the request returned.

    Overrides expire. A forgotten manual command must not leave a
    humidifier running indefinitely.
    """
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    state = data.get("state")
    duration = data.get("duration_seconds", MANUAL_OVERRIDE_DEFAULT_SECONDS)

    if not name or not isinstance(name, str):
        return error_response("Missing or invalid 'name'", "missing_field", 400)
    if state is not None and not isinstance(state, bool):
        return error_response(
            "Invalid 'state' (must be boolean, or null to clear)", "invalid_state", 400)

    output = find_output(name)
    if not output:
        valid = ", ".join(o["name"] for o in get_outputs())
        return error_response(f"Unknown output name '{name}'. Valid names: {valid}", "unknown_output", 404)

    try:
        duration = int(duration)
    except (TypeError, ValueError):
        return error_response("'duration_seconds' must be an integer", "invalid_duration", 400)
    if duration < 1 or duration > MANUAL_OVERRIDE_MAX_SECONDS:
        return error_response(
            f"'duration_seconds' must be between 1 and {MANUAL_OVERRIDE_MAX_SECONDS}",
            "invalid_duration", 400,
        )

    key = manual_override_key(name)
    try:
        if state is None:
            get_redis().delete(key)
        else:
            get_redis().set(key, "true" if state else "false", ex=duration)
    except Exception as e:
        return error_response(f"Failed to publish the override: {e}", "save_failed", 500)

    return jsonify(
        {
            "success": True,
            "output": {
                "name": output.get("name"),
                "label": output.get("label"),
                "pin": pin_sources().get(name),
                "redis_key": output.get("redis_key"),
                "requested_state": state,
                "expires_in_seconds": None if state is None else duration,
            },
            "note": "The control loop applies the override; it expires on its own."
            if state is not None
            else "Output handed back to automatic control.",
        }
    )


@bp.route("/api/outputs/desired", methods=["GET"])
def get_desired_outputs():
    """
    The full desired output state, for a remote relay node to poll.

    Every output is always named, so the node never has to merge a partial
    update against its own idea of the current state.

    When the control loop's last write is too old, the answer is everything
    off with `failsafe` set. A node that kept applying a stale command would
    leave a humidifier running long after the loop died, and the loop's own
    failsafe cannot help once the loop is what stopped.
    """
    off = {name: False for name in output_names()}

    # The virtual remote rides on this poll (autocann.hardware.ir_remote): the
    # node repeats the id of the last press it sent as `ir_ack`, which clears
    # it, and is handed the next one as `ir`. A GET that writes, but this is
    # the node's only channel. Never fatal: the outputs matter more.
    ir = None
    try:
        ir_remote.acknowledge(get_redis(), request.args.get("ir_ack"))
        ir = ir_remote.node_payload(get_redis())
    except Exception:
        ir = None

    def failsafe(reason):
        # No press in failsafe: everything is off, so the humidifier has no
        # power to receive it. It expires and the dashboard says it did not go.
        return jsonify({
            "outputs": off,
            "ir": None,
            "failsafe": True,
            "reason": reason,
            "max_age_seconds": DESIRED_OUTPUTS_MAX_AGE_SECONDS,
        })

    try:
        raw = get_redis().get(DESIRED_OUTPUTS_KEY)
    except Exception as e:
        return failsafe(f"redis unavailable: {e}")
    if not raw:
        return failsafe("no desired state published")

    try:
        payload = json.loads(raw)
        outputs = payload["outputs"]
        age = int(datetime.now(ARGENTINA_TZ).timestamp()) - int(payload["ts"])
    except Exception as e:
        return failsafe(f"unreadable desired state: {e}")

    if age > DESIRED_OUTPUTS_MAX_AGE_SECONDS:
        return failsafe(f"stale desired state ({age}s old)")

    # Names the loop did not publish default to off rather than being
    # omitted: a node must never have to guess what to do with an output.
    return jsonify({
        "outputs": {name: bool(outputs.get(name, False)) for name in off},
        "ir": ir,
        "failsafe": False,
        "age_seconds": age,
        "max_age_seconds": DESIRED_OUTPUTS_MAX_AGE_SECONDS,
    })


# ===============================
# Quiet hours
# ===============================

QUIET_HOURS_SETTING = "quiet_hours"

@bp.route("/api/quiet-hours", methods=["GET"])
def get_quiet_hours():
    """
    The per-output quiet windows, and which outputs a window can affect.

    `automatic_outputs` is served rather than assumed: a window on an output
    nothing drives automatically would do nothing, and the dashboard should
    say so instead of offering a setting that is quietly inert.
    """
    try:
        stored = get_setting(QUIET_HOURS_SETTING, {}) or {}
        windows = windows_from_dict(stored)
        return jsonify(
            {
                "windows": {name: w.to_dict() for name, w in windows.items()},
                "automatic_outputs": sorted(ACTION_OUTPUTS.values()),
                "outputs": [
                    {"name": o["name"], "label": o["label"]} for o in get_outputs()
                ],
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/quiet-hours", methods=["PUT"])
def update_quiet_hours():
    """
    Replace the quiet windows.

    Body: {"windows": {"<output>": {"enabled": bool, "start": "HH:MM",
    "end": "HH:MM", "max_humidity": number|null}}}

    `max_humidity` is the way out: at or above it the window is ignored.
    Leaving it null is a hard block, which is a real choice — a night at
    85 % is mould, and silence is not worth a crop unless you say so.
    """
    data = request.get_json(silent=True) or {}
    raw = data.get("windows")
    if not isinstance(raw, dict):
        return jsonify({"error": "Missing or invalid 'windows' (must be an object)"}), 400

    cleaned = {}
    for name, value in raw.items():
        if not find_output(name):
            valid = ", ".join(output_names())
            return jsonify(
                {"error": f"Unknown output name '{name}'. Valid names: {valid}"}), 400
        if not isinstance(value, dict):
            return jsonify({"error": f"'{name}' must be an object"}), 400

        enabled = bool(value.get("enabled"))
        start, end = value.get("start"), value.get("end")
        if enabled:
            # Only checked when the window is on. Half-filled configuration
            # that nobody has switched on yet is a draft, not an error.
            if parse_hhmm(start) is None or parse_hhmm(end) is None:
                return jsonify(
                    {"error": f"'{name}': 'start' y 'end' tienen que ser HH:MM"}), 400
            if parse_hhmm(start) == parse_hhmm(end):
                return jsonify(
                    {"error": f"'{name}': una franja de ancho cero no silencia nada"}), 400

        max_humidity = value.get("max_humidity")
        if max_humidity is not None:
            try:
                max_humidity = float(max_humidity)
            except (TypeError, ValueError):
                return jsonify({"error": f"'{name}': 'max_humidity' debe ser un número"}), 400
            if not 0 <= max_humidity <= 100:
                return jsonify({"error": f"'{name}': 'max_humidity' fuera de rango (0-100)"}), 400

        cleaned[name] = QuietWindow(
            enabled=enabled,
            start=parse_hhmm(start),
            end=parse_hhmm(end),
            max_humidity=max_humidity,
        ).to_dict()

    if not set_setting(QUIET_HOURS_SETTING, cleaned):
        return jsonify({"error": "Failed to store the quiet hours"}), 500

    return jsonify({
        "success": True,
        "windows": cleaned,
        "note": "El lazo la toma en menos de 30 s.",
    })
