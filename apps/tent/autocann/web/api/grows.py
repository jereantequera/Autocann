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
    get_stage_timeline,
    set_active_grow,
    update_grow_stage,
)
from autocann.store.grow_stages import profile_stage_table, stage_table
from autocann.store.seeds import BUILTIN_PROFILE_ID
from autocann.web.errors import error_response

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
        return error_response("No active grow found", "no_active_grow", 404)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/grows", methods=["POST"])
def create_new_grow():
    try:
        data = request.get_json()
        if not data:
            return error_response("Name is required", "invalid_body", 400)
        if "name" not in data:
            return error_response("Name is required", "invalid_name", 400)

        name = data["name"]
        notes = data.get("notes", "")
        # The new grow runs the stages of the profile it is made from; the
        # built-in one unless the request names another.
        profile_id = data.get("profile_id") or BUILTIN_PROFILE_ID
        table = profile_stage_table(profile_id)
        if not table:
            return error_response(f'Unknown profile "{profile_id}"', "unknown_profile", 400)
        stage = data.get("stage", "early_veg" if "early_veg" in table else next(iter(table)))

        if stage not in table:
            return error_response(f'Invalid stage. Use one of: {", ".join(table)}', "invalid_stage", 400)

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

@bp.route("/api/grows/<int:grow_id>/stage", methods=["PUT"])
def update_stage_endpoint(grow_id: int):
    try:
        data = request.get_json()
        if not data or "stage" not in data:
            return error_response("Stage is required", "missing_field", 400)

        stage = data["stage"]
        table = stage_table(grow_id)
        if stage not in table:
            return error_response(f'Invalid stage. Use one of: {", ".join(table)}', "invalid_stage", 400)

        success = update_grow_stage(grow_id, stage, notes=data.get("notes"))
        if success:
            return jsonify({"success": True, "message": f"Grow {grow_id} stage updated to {stage}"})
        return jsonify({"error": "Failed to update stage"}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500
