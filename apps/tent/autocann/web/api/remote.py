"""
The humidifier's virtual remote: the buttons, and pressing one.

A press does not reach the humidifier from here. It is parked in Redis
(`autocann.hardware.ir_remote`) and the relay node picks it up on its next
poll of `GET /api/outputs/desired`, sends it through its IR LED, and
acknowledges it on the poll after. The dashboard reads that back here to say
whether the press went out.
"""

from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, jsonify, request

from autocann.config import DESIRED_OUTPUTS_KEY, DESIRED_OUTPUTS_MAX_AGE_SECONDS
from autocann.hardware.ir_remote import (
    IR_BUTTONS,
    IR_COMMAND_TTL_SECONDS,
    IR_TARGET_OUTPUT,
    enqueue,
    find_button,
    last_sent,
    pending_command,
)
from autocann.time import ARGENTINA_TZ
from autocann.web.deps import get_redis
from autocann.web.errors import error_response

bp = Blueprint("remote", __name__)


def _humidifier_powered(redis) -> bool:
    """
    Whether the node is being told to power the humidifier right now.

    Judged on what the node is served, not on the relay's last reported state:
    a stale or missing desired state is served as everything off, so it is off
    here too.
    """
    try:
        payload = json.loads(redis.get(DESIRED_OUTPUTS_KEY) or "null")
        age = int(datetime.now(ARGENTINA_TZ).timestamp()) - int(payload["ts"])
        return age <= DESIRED_OUTPUTS_MAX_AGE_SECONDS and bool(payload["outputs"].get(IR_TARGET_OUTPUT))
    except Exception:
        return False


def _public(command):
    if not command:
        return None
    return {k: command.get(k) for k in ("id", "button", "queued_at", "sent_at") if k in command}


@bp.route("/api/ir-remote", methods=["GET"])
def get_ir_remote():
    redis = get_redis()
    try:
        pending, last, powered = pending_command(redis), last_sent(redis), _humidifier_powered(redis)
    except Exception as e:
        return error_response(f"Redis unavailable: {e}", "save_failed", 500)
    return jsonify({
        "buttons": [{"key": b.key, "label": b.label, "hint": b.hint} for b in IR_BUTTONS],
        "target_output": IR_TARGET_OUTPUT,
        "powered": powered,
        "pending": _public(pending),
        "last": _public(last),
        "ttl_seconds": IR_COMMAND_TTL_SECONDS,
    })


@bp.route("/api/ir-remote/press", methods=["POST"])
def press_ir_button():
    """
    Body: {"button": "<key>"}. Answers with the queued command's id.

    Refused while the humidifier has no power, because a pulse would reach a
    dead box and the person would think it went through; and while another
    press is still waiting for the node, because POWER is a toggle and two
    pulses undo each other.
    """
    data = request.get_json(silent=True) or {}
    key = data.get("button")
    if not key or not isinstance(key, str):
        return error_response("Missing or invalid 'button'", "missing_field", 400)

    button = find_button(key)
    if not button:
        valid = ", ".join(b.key for b in IR_BUTTONS)
        return error_response(f"Unknown button '{key}'. Valid buttons: {valid}", "unknown_button", 404)

    redis = get_redis()
    try:
        if not _humidifier_powered(redis):
            return error_response(
                f"The humidifier has no power ({IR_TARGET_OUTPUT} is off)", "humidifier_off", 409)
        command = enqueue(redis, button)
    except Exception as e:
        return error_response(f"Failed to queue the press: {e}", "save_failed", 500)

    if command is None:
        return error_response("Another press is still waiting for the node", "ir_busy", 409)

    return jsonify({"success": True, "command": _public(command), "ttl_seconds": IR_COMMAND_TTL_SECONDS})
