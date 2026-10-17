"""
Grow management: list, create, end, activate, timeline and stage changes.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from autocann.db import (
    create_grow,
    end_grow,
    get_active_grow,
    get_all_grows,
    get_photoperiod,
    get_stage_timeline,
    set_active_grow,
    set_photoperiod,
    update_grow_stage,
)
from autocann.store.grow_stages import profile_stage_table, stage_table
from autocann.store.seeds import BUILTIN_PROFILE_ID

bp = Blueprint("grows", __name__)


# ===============================
# Grow Management Endpoints
# ===============================

@bp.route("/api/grows", methods=["GET"])
def list_grows():
    try:
        grows = get_all_grows()
        return jsonify({"grows": grows, "count": len(grows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/grows/active", methods=["GET"])
def active_grow():
    try:
        grow = get_active_grow()
        if grow:
            return jsonify(grow)
        return jsonify({"error": "No active grow found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/grows", methods=["POST"])
def create_new_grow():
    try:
        data = request.get_json()
        if not data or "name" not in data:
            return jsonify({"error": "Name is required"}), 400

        name = data["name"]
        notes = data.get("notes", "")
        # The new grow runs the stages of the profile it is made from; the
        # built-in one unless the request names another.
        profile_id = data.get("profile_id") or BUILTIN_PROFILE_ID
        table = profile_stage_table(profile_id)
        if not table:
            return jsonify({"error": f'Unknown profile "{profile_id}"'}), 400
        stage = data.get("stage", "early_veg" if "early_veg" in table else next(iter(table)))

        if stage not in table:
            return jsonify({"error": f'Invalid stage. Use one of: {", ".join(table)}'}), 400

        grow_id = create_grow(name, stage, notes, profile_id=profile_id)
        if grow_id:
            return jsonify({"success": True, "grow_id": grow_id, "message": f'Grow "{name}" created successfully'}), 201
        return jsonify({"error": "Failed to create grow"}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/grows/<int:grow_id>/end", methods=["POST"])
def finish_grow(grow_id: int):
    try:
        success = end_grow(grow_id)
        if success:
            return jsonify({"success": True, "message": f"Grow {grow_id} ended successfully"})
        return jsonify({"error": "Failed to end grow"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/grows/<int:grow_id>/activate", methods=["POST"])
def activate_grow_endpoint(grow_id: int):
    try:
        success = set_active_grow(grow_id)
        if success:
            return jsonify({"success": True, "message": f"Grow {grow_id} activated successfully"})
        return jsonify({"error": "Failed to activate grow"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/grows/<int:grow_id>/timeline", methods=["GET"])
def grow_timeline(grow_id: int):
    """
    Stage periods for a grow, with how many days each one lasted.

    Days are calendar days in the local timezone, and the day a stage starts
    is day 1.
    """
    try:
        return jsonify({"grow_id": grow_id, "timeline": get_stage_timeline(grow_id)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/grows/<int:grow_id>/photoperiod", methods=["GET"])
def get_photoperiod_endpoint(grow_id: int):
    """The local hours the lights come on and go off, and whether the grow set them."""
    try:
        found = get_photoperiod(grow_id)
        if found is None:
            return jsonify({"error": f"Grow {grow_id} not found"}), 404
        return jsonify(found)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/grows/<int:grow_id>/photoperiod", methods=["PUT"])
def set_photoperiod_endpoint(grow_id: int):
    """
    Set the photoperiod: `{"on": 6, "off": 0}`, hours 0-23 in local time.

    A window that crosses midnight is fine (on 18, off 12). `on` and `off` must
    differ, since equal hours would be no window at all.
    """
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or "on" not in data or "off" not in data:
            return jsonify({"error": "'on' and 'off' are required"}), 400
        try:
            saved = set_photoperiod(grow_id, data["on"], data["off"])
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        if saved is None:
            return jsonify({"error": f"Grow {grow_id} not found"}), 404
        return jsonify(saved)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/grows/<int:grow_id>/stage", methods=["PUT"])
def update_stage_endpoint(grow_id: int):
    try:
        data = request.get_json()
        if not data or "stage" not in data:
            return jsonify({"error": "Stage is required"}), 400

        stage = data["stage"]
        table = stage_table(grow_id)
        if stage not in table:
            return jsonify({"error": f'Invalid stage. Use one of: {", ".join(table)}'}), 400

        success = update_grow_stage(grow_id, stage, notes=data.get("notes"))
        if success:
            return jsonify({"success": True, "message": f"Grow {grow_id} stage updated to {stage}"})
        return jsonify({"error": "Failed to update stage"}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500
