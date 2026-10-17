"""
Analytics: the VPD band a stage allows, VPD and weekly scores, and anomalies.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from autocann.control.vpd_math import (
    LEAF_TEMP_OFFSET_C,
    humidity_range_bounds_for_stage,
)
from autocann.db import (
    detect_anomalies,
    get_active_grow,
    get_vpd_score,
    get_weekly_report,
)
from autocann.store.grow_stages import active_stage_table
from autocann.web.alert_owners import reframe_history_anomalies
from autocann.web.deps import get_redis
from autocann.web.diagnostics import health_snapshot
from autocann.web.helpers import grow_scope, requested_grow

bp = Blueprint("analytics", __name__)


#: Air temperature span of the VPD chart. Wide enough to show every band the
#: dashboard can draw; 0.5 °C steps match the resolution of the sensors.
BAND_TEMP_MIN = 18.0
BAND_TEMP_MAX = 32.0
BAND_TEMP_STEP = 0.5


@bp.route("/api/vpd-band", methods=["GET"])
def get_vpd_band():
    """
    The humidity band a stage allows, at each air temperature of the chart.

    Computed here, with the controller's own functions and leaf offset, and
    not in the browser: a chart that re-implemented the Tetens math would
    drift from what the loop actually does, and the operator would be
    reading a band the tent is not held to. Each point is what
    `resolve_setpoint` gets for that air temperature.
    """
    table = active_stage_table()
    stage = (request.args.get("stage") or "").strip()
    if not stage:
        grow = get_active_grow()
        if not grow:
            return jsonify({"error": "No active grow found"}), 404
        stage = grow["stage"]
    if stage not in table:
        return jsonify({"error": f'Invalid stage. Use one of: {", ".join(table)}'}), 400

    # The bands are the active grow's own copy of the stage, not the constants.
    row = table[stage]
    explicit = (row["rh_min"], row["rh_max"]) if row["rh_min"] is not None else None
    vpd_band = (row["vpd_min"], row["vpd_max"]) if row["vpd_min"] is not None else None
    point_count = round((BAND_TEMP_MAX - BAND_TEMP_MIN) / BAND_TEMP_STEP) + 1
    points = []
    for step in range(point_count):
        temperature = round(BAND_TEMP_MIN + step * BAND_TEMP_STEP, 1)
        if explicit is not None:
            low, high = explicit
        else:
            low, high = sorted(
                humidity_range_bounds_for_stage(
                    stage,
                    temperature,
                    round(temperature - LEAF_TEMP_OFFSET_C, 1),
                    vpd_band=vpd_band,
                )
            )
        points.append({
            "temperature": temperature,
            "humidity_low": round(low, 1),
            "humidity_high": round(high, 1),
        })

    vpd_min = vpd_max = None
    if explicit is None:
        vpd_min, vpd_max = vpd_band

    return jsonify({
        "stage": stage,
        "kind": "humidity" if explicit is not None else "vpd",
        "vpd_min": vpd_min,
        "vpd_max": vpd_max,
        "leaf_offset_c": LEAF_TEMP_OFFSET_C,
        "temp_min": BAND_TEMP_MIN,
        "temp_max": BAND_TEMP_MAX,
        "points": points,
    })


# ===============================
# Analytics Endpoints
# ===============================

@bp.route("/api/vpd-score", methods=["GET"])
def vpd_score_endpoint():
    """
    Get VPD score (% of time in optimal range).

    Query params: days (int, default 7), grow_id (optional). `all` is
    refused here on purpose: the score measures time inside *a stage's*
    band, and one number over several grows scores readings against bands
    they were never held to.
    """
    try:
        days = request.args.get("days", default=7, type=int)
        try:
            grow_id = requested_grow()
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        score = get_vpd_score(days=days, grow_id=grow_id)
        if score.get("error"):
            return jsonify(score)
        return jsonify({**score, **grow_scope(grow_id)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/weekly-report", methods=["GET"])
def weekly_report_endpoint():
    """
    Get comprehensive weekly report.

    Query params: grow_id (optional). Scoped to one grow, including the
    previous-week comparison: a trend measured against a week the tent
    stood empty is not a trend.
    """
    try:
        try:
            grow_id = requested_grow()
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        report = get_weekly_report(grow_id=grow_id)
        if report.get("error"):
            return jsonify(report)
        return jsonify({**report, **grow_scope(grow_id)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/anomalies", methods=["GET"])
def anomalies_endpoint():
    """
    Detect anomalies in sensor data.
    Query params: hours (int, default 24), grow_id (optional)
    """
    try:
        hours = request.args.get("hours", default=24, type=int)
        try:
            grow_id = requested_grow()
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        anomalies = detect_anomalies(hours=hours, grow_id=grow_id)
        # El detector mira la base, que es el lugar correcto para buscar un
        # hueco en el histórico y el equivocado para buscar un sensor
        # muerto: los dos son la misma cantidad de filas. Con el sistema en
        # pausa no hay filas a propósito, y eso salía como un "sensor
        # posiblemente desconectado" en rojo arriba de dos tarjetas verdes.
        return jsonify(reframe_history_anomalies(
            anomalies, health_snapshot(get_redis())))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
