"""
The irrigation schedule: the interval of the active grow, and when the next
watering is due.
"""

from __future__ import annotations

from datetime import date, datetime

from flask import Blueprint, jsonify, request

from autocann.control.irrigation import MAX_INTERVAL_DAYS, MIN_INTERVAL_DAYS, next_due
from autocann.db import (
    get_active_grow,
    get_irrigation_interval,
    last_irrigation_date,
    set_irrigation_interval,
)
from autocann.time import ARGENTINA_TZ

bp = Blueprint("irrigation_schedule", __name__)


@bp.route("/api/irrigation/next", methods=["GET"])
def irrigation_next():
    """
    The interval of the active grow, its last watering, and when the next is due.

    Without an interval the answer is `{"configured": false}`: the card asks for
    one instead of guessing.
    """
    try:
        grow = get_active_grow()
        if not grow:
            return jsonify({"error": "No active grow found"}), 404

        interval = get_irrigation_interval(grow["id"])
        if interval is None:
            return jsonify({"configured": False, "grow_id": grow["id"]})

        last = last_irrigation_date(grow["id"])
        today = datetime.now(ARGENTINA_TZ).date()
        last_day = date.fromisoformat(last) if last else None
        return jsonify({
            "configured": True,
            "grow_id": grow["id"],
            "interval_days": interval,
            "last_irrigation_date": last,
            "next": next_due(last_day, interval, today),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/irrigation/interval", methods=["PUT"])
def save_irrigation_interval():
    """
    Set the active grow's interval, in days. `null` clears it.

    Anything that is not a whole number from 1 to 30 (or null) is a 400, not a
    silent clamp: "2.5 días" is a typo to be told about.
    """
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or "days" not in data:
            return jsonify({"error": "Se espera {'days': número de 1 a 30, o null}"}), 400

        days = data["days"]
        if days is not None and (
            isinstance(days, bool)
            or not isinstance(days, int)
            or not MIN_INTERVAL_DAYS <= days <= MAX_INTERVAL_DAYS
        ):
            return jsonify({
                "error": f"days debe ser un entero de {MIN_INTERVAL_DAYS} a "
                         f"{MAX_INTERVAL_DAYS}, o null para quitarlo",
            }), 400

        grow = get_active_grow()
        if not grow:
            return jsonify({"error": "No active grow found"}), 404

        if not set_irrigation_interval(grow["id"], days):
            return jsonify({"error": "No existe ese cultivo"}), 404
        return jsonify({"success": True, "grow_id": grow["id"], "interval_days": days})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
