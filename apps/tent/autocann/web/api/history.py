"""
Sensor history and the period views built on it.
"""

from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, jsonify, request

from autocann.control.sampling import insert_gaps
from autocann.db import (
    get_aggregated_data,
    get_database_stats,
    get_latest_sensor_data,
    get_period_summary,
    get_sensor_data_range,
)
from autocann.time import ARGENTINA_TZ
from autocann.web.deps import get_redis
from autocann.web.helpers import grow_scope, requested_grow

bp = Blueprint("history", __name__)


#: How often the control loop writes a sample. Used to tell a real outage from
#: the normal spacing between points.
SAMPLE_INTERVAL_SECONDS = 300


def _with_gaps(points, expected_interval_seconds):
    """
    Insert break markers so the charts stop drawing straight lines across holes.

    A six-hour outage used to join its two ends with a line, which reads as "the
    temperature fell smoothly" rather than "there is no data here". Each marker
    carries a datetime so it still gets an axis label, and no measurements, so
    every series plots a gap at that point.

    `grow_id` breaks the series too. Two grows one after the other are not one
    continuous curve: without this, a 90-day window spanning the end of one
    crop and the start of the next drew a single line through the changeover,
    which is the most misleading shape this chart can take.
    """
    marked = insert_gaps(points, expected_interval_seconds, series_key="grow_id")
    for point in marked:
        if point.get("gap") and point.get("timestamp") is not None:
            point["datetime"] = datetime.fromtimestamp(
                point["timestamp"], ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return marked


@bp.route("/api/current-data", methods=["GET"])
def get_current_data():
    """
    Endpoint to get current sensor data from Redis.
    """
    data = get_redis().get("sensors")
    if data:
        return jsonify(json.loads(data))
    return jsonify({"error": "No current data available"}), 404


@bp.route("/api/sensor-history", methods=["GET"])
def get_sensor_history():
    """
    Endpoint to get sensor history from SQLite database.

    Scoped to a grow. `grow_id=<n>` picks one, `grow_id=all` asks for every
    grow at once, and leaving it out means the active grow — which is what
    the rest of the dashboard is about, so it is what a chart with no
    selector should show. Filtering by time alone used to put ten months of
    an empty tent and a real crop on the same curve as soon as the window
    reached back past the changeover.
    """
    try:
        period = request.args.get("period")
        start = request.args.get("start", type=int)
        end = request.args.get("end", type=int)
        limit = request.args.get("limit", type=int)
        aggregate = request.args.get("aggregate", type=int)
        try:
            grow_id = requested_grow()
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        scope = grow_scope(grow_id)

        if period:
            current_time = datetime.now(ARGENTINA_TZ)
            current_timestamp = int(current_time.timestamp())

            periods = {
                "1h": 3600,
                "6h": 6 * 3600,
                "12h": 12 * 3600,
                "24h": 24 * 3600,
                "7d": 7 * 24 * 3600,
                "30d": 30 * 24 * 3600,
                "90d": 90 * 24 * 3600,
            }

            if period not in periods:
                return jsonify({"error": f'Invalid period. Use one of: {", ".join(periods.keys())}'}), 400

            start = current_timestamp - periods[period]
            end = current_timestamp

        if start is None and end is None:
            if limit is None:
                limit = 100
            data = get_latest_sensor_data(limit=limit, grow_id=grow_id)
            return jsonify({"data": data, "count": len(data),
                            "aggregated": False, **scope})

        # Fill in whichever bound is missing: a None would reach the SQL
        # comparison and silently match nothing.
        now = int(datetime.now(ARGENTINA_TZ).timestamp())
        if end is None:
            end = now
        if start is None:
            start = end - 24 * 3600

        if start > end:
            return jsonify({"error": "'start' must be earlier than 'end'"}), 400

        if aggregate:
            if aggregate < 1:
                return jsonify({"error": "'aggregate' must be a positive number of seconds"}), 400
            data = _with_gaps(
                get_aggregated_data(start, end, aggregate, grow_id), aggregate)
            return jsonify(
                {
                    "data": data,
                    "count": len(data),
                    "start": start,
                    "end": end,
                    "aggregated": True,
                    "interval_seconds": aggregate,
                    **scope,
                }
            )

        # get_sensor_data_range returns newest first; the gap check needs
        # chronological order, and so do the charts. Sorted by grow as well,
        # so two grows arrive as two runs rather than interleaved — which is
        # what lets the gap marker land between them instead of on every
        # other point.
        data = _with_gaps(
            sorted(get_sensor_data_range(start, end, limit, grow_id),
                   key=lambda r: (r["grow_id"], r["timestamp"])),
            SAMPLE_INTERVAL_SECONDS,
        )
        return jsonify({"data": data, "count": len(data), "start": start,
                        "end": end, "aggregated": False, **scope})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/database-stats", methods=["GET"])
def database_stats():
    try:
        stats = get_database_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/period-summary", methods=["GET"])
def period_summary():
    """
    Get summary statistics (avg, min, max) for a time period.

    Query params: hours (float) or days (int), grow_id (optional: an id,
    `all`, or absent for the active grow).
    """
    try:
        hours = request.args.get("hours", type=float)
        days = request.args.get("days", type=int)
        try:
            grow_id = requested_grow()
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        current_time = datetime.now(ARGENTINA_TZ)
        end_timestamp = int(current_time.timestamp())

        if hours is not None:
            start_timestamp = end_timestamp - int(hours * 3600)
        elif days is not None:
            start_timestamp = end_timestamp - (days * 24 * 3600)
        else:
            # Default to last 24 hours
            start_timestamp = end_timestamp - (24 * 3600)

        summary = get_period_summary(start_timestamp, end_timestamp, grow_id)
        return jsonify({**summary, **grow_scope(grow_id)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/history/aggregated", methods=["GET"])
def get_history_aggregated():
    try:
        days = request.args.get("days", default=7, type=int)
        interval = request.args.get("interval", default="hourly", type=str)
        try:
            grow_id = requested_grow()
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        interval_map = {"hourly": 3600, "6hourly": 6 * 3600, "daily": 24 * 3600}
        if interval not in interval_map:
            return jsonify({"error": f'Invalid interval. Use one of: {", ".join(interval_map.keys())}'}), 400

        interval_seconds = interval_map[interval]
        current_time = datetime.now(ARGENTINA_TZ)
        end_timestamp = int(current_time.timestamp())
        start_timestamp = end_timestamp - (days * 24 * 3600)

        data = _with_gaps(
            get_aggregated_data(start_timestamp, end_timestamp, interval_seconds, grow_id),
            interval_seconds,
        )

        return jsonify(
            {
                "data": data,
                "count": len(data),
                "days": days,
                "interval": interval,
                "start_datetime": datetime.fromtimestamp(start_timestamp, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "end_datetime": datetime.fromtimestamp(end_timestamp, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                **grow_scope(grow_id),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
