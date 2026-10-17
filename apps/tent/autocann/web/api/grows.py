"""
Grow management: list, create, end, activate, timeline and stage changes.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from autocann.control.vpd_math import (
    STAGES,
)
from autocann.db import (
    create_grow,
    end_grow,
    get_active_grow,
    get_all_grows,
    get_stage_timeline,
    set_active_grow,
    update_grow_stage,
)

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
        stage = data.get("stage", "early_veg")
        notes = data.get("notes", "")

        if stage not in STAGES:
            return jsonify({"error": f'Invalid stage. Use one of: {", ".join(STAGES)}'}), 400

        grow_id = create_grow(name, stage, notes)
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
            return jsonify({"error": "Stage is required"}), 400

        stage = data["stage"]
        if stage not in STAGES:
            return jsonify({"error": f'Invalid stage. Use one of: {", ".join(STAGES)}'}), 400

        success = update_grow_stage(grow_id, stage, notes=data.get("notes"))
        if success:
            return jsonify({"success": True, "message": f"Grow {grow_id} stage updated to {stage}"})
        return jsonify({"error": "Failed to update stage"}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500
