"""
Feeding: the weekly feed plan, what to feed today, and the irrigation log.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from autocann.control.feeding import (
    chart_columns,
    effective_week,
    plan_for_week,
    scale_to_volume,
    water_only_reason,
    weeks_in_stage,
)
from autocann.control.vpd_math import (
    STAGE_EXPECTED_DAYS,
)
from autocann.db import (
    delete_irrigation,
    get_active_grow,
    get_feed_plan,
    get_irrigations,
    last_irrigation_litres,
    record_irrigation,
    replace_feed_plan,
)

bp = Blueprint("feeding", __name__)


# ===============================
# Feeding Endpoints
# ===============================

@bp.route("/api/feed-plan", methods=["GET"])
def feed_plan():
    """
    The feed chart, plus the grid shape the editor needs to draw it.

    The columns come from the stage lengths in vpd_math.py, so the editor
    never holds its own idea of how long flowering is.
    """
    try:
        return jsonify(
            {
                "products": get_feed_plan(),
                "columns": chart_columns(),
                "weeks_per_stage": {
                    stage: weeks_in_stage(stage) for stage in STAGE_EXPECTED_DAYS
                },
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/feed-plan", methods=["PUT"])
def save_feed_plan():
    """Replace the whole chart with the grid the editor is showing."""
    try:
        data = request.get_json(silent=True)
        if not data or not isinstance(data.get("products"), list):
            return jsonify({"error": "Se espera {'products': [...]}"}), 400

        if replace_feed_plan(data["products"]):
            return jsonify({"success": True, "products": get_feed_plan()})
        return jsonify({"error": "No se pudo guardar el plan"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/feeding/today", methods=["GET"])
def feeding_today():
    """
    What the chart says to pour right now, already scaled to litres.

    The stage and the day inside it come from the active grow, so this never
    asks the grower to re-enter dates the backend already knows.
    """
    try:
        grow = get_active_grow()
        if not grow:
            return jsonify({"error": "No active grow found"}), 404

        stage = grow.get("stage")
        day = grow.get("day_in_stage")
        week = effective_week(stage, day) if stage else 0
        litres = request.args.get("litres", type=float)
        if litres is None:
            litres = last_irrigation_litres(grow["id"])

        due = scale_to_volume(plan_for_week(get_feed_plan(), stage, week), litres)
        return jsonify(
            {
                "grow_id": grow["id"],
                "stage": stage,
                "day_in_stage": day,
                "week": week,
                "weeks_in_stage": weeks_in_stage(stage) if stage else 0,
                "litres": litres,
                "items": due,
                # A week with nothing due is an instruction, and which one
                # depends on the stage: a seedling drinks plain water, the
                # last week of flowering is the flush.
                "water_only": water_only_reason(stage, week, due),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/irrigations", methods=["GET"])
def list_irrigations():
    try:
        grow_id = request.args.get("grow_id", type=int)
        if grow_id is None:
            grow = get_active_grow()
            if not grow:
                return jsonify({"error": "No active grow found"}), 404
            grow_id = grow["id"]

        limit = min(request.args.get("limit", default=100, type=int), 500)
        return jsonify({"grow_id": grow_id, "irrigations": get_irrigations(grow_id, limit)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/irrigations", methods=["POST"])
def create_irrigation():
    """
    Log a watering.

    The stage and week are stamped from the active grow rather than taken
    from the request: the log should say what the plant was actually going
    through, not what the browser believed.
    """
    try:
        data = request.get_json(silent=True) or {}
        try:
            litres = float(data.get("litres"))
        except (TypeError, ValueError):
            return jsonify({"error": "litres es obligatorio y numérico"}), 400
        if litres <= 0:
            return jsonify({"error": "litres debe ser mayor que cero"}), 400

        items = data.get("items")
        if items is not None and not isinstance(items, list):
            return jsonify({"error": "items debe ser una lista"}), 400

        grow = get_active_grow()
        if not grow:
            return jsonify({"error": "No active grow found"}), 404

        stage = grow.get("stage")
        week = effective_week(stage, grow.get("day_in_stage")) if stage else None

        irrigation_id = record_irrigation(
            grow_id=grow["id"],
            litres=litres,
            items=items or [],
            date=data.get("date"),
            ph=data.get("ph"),
            ec=data.get("ec"),
            stage=stage,
            week=week,
            notes=data.get("notes", ""),
        )
        if irrigation_id:
            return jsonify({"success": True, "irrigation_id": irrigation_id}), 201
        return jsonify({"error": "No se pudo registrar el riego"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/irrigations/<int:irrigation_id>", methods=["DELETE"])
def remove_irrigation(irrigation_id: int):
    try:
        if delete_irrigation(irrigation_id):
            return jsonify({"success": True})
        return jsonify({"error": "No existe ese riego"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
