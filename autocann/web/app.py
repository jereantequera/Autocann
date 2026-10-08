from __future__ import annotations

import json
from datetime import datetime

import redis
from flask import Flask, jsonify, render_template, request

from autocann import __version__
from autocann.config import indoor_sensor_key, is_valid_sensor_id, redis_config_from_env
from autocann.control.sampling import insert_gaps
from autocann.control.vpd_math import STAGES, VPD_RANGES, calculate_vpd, humidity_range_for_stage
from autocann.db import (
    create_grow,
    detect_anomalies,
    end_grow,
    get_active_grow,
    get_aggregated_data,
    get_all_calibrations,
    get_all_grows,
    get_database_stats,
    get_latest_sensor_data,
    get_period_summary,
    get_sensor_data_range,
    get_stage_timeline,
    get_vpd_score,
    get_weekly_report,
    set_active_grow,
    set_calibration,
    update_grow_stage,
)
from autocann.hardware.outputs import find_output, get_outputs, manual_override_key, parse_bool_flag
from autocann.paths import STATIC_DIR, TEMPLATES_DIR
from autocann.time import ARGENTINA_TZ

#: Default and maximum lifetime of a manual output override.
MANUAL_OVERRIDE_DEFAULT_SECONDS = 15 * 60
MANUAL_OVERRIDE_MAX_SECONDS = 2 * 60 * 60


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
    """
    marked = insert_gaps(points, expected_interval_seconds)
    for point in marked:
        if point.get("gap"):
            point["datetime"] = datetime.fromtimestamp(
                point["timestamp"], ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return marked


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(TEMPLATES_DIR),
        static_folder=str(STATIC_DIR),
        static_url_path="/static",
    )

    @app.context_processor
    def inject_version():
        # Appended to static URLs so a deploy busts the browser cache without
        # having to serve the assets with no-cache.
        return {"asset_version": __version__}
    redis_cfg = redis_config_from_env()
    redis_client = redis.Redis(host=redis_cfg.host, port=redis_cfg.port, db=redis_cfg.db)

    @app.route("/")
    def index():
        """
        Serve the main dashboard page.
        """
        return render_template("index.html")

    @app.route("/api/config", methods=["GET"])
    def get_config():
        """
        Stage configuration for the dashboard.

        The ranges used to be a hardcoded copy in the template, which is how the
        template and vpd_math.py drifted apart. Serving them keeps one source.
        """
        return jsonify(
            {
                "stages": list(STAGES),
                "vpd_ranges": {
                    stage: {"min": low, "max": high} for stage, (low, high) in VPD_RANGES.items()
                },
                "humidity_ranges": {
                    stage: ({"min": band[0], "max": band[1]} if band else None)
                    for stage in STAGES
                    for band in [humidity_range_for_stage(stage)]
                },
                "stage_names": {
                    "early_veg": "Vegetativo Temprano",
                    "late_veg": "Vegetativo Tardío",
                    "flowering": "Floración",
                    "dry": "Secado",
                },
            }
        )

    @app.route("/api/current-data", methods=["GET"])
    def get_current_data():
        """
        Endpoint to get current sensor data from Redis.
        """
        data = redis_client.get("sensors")
        if data:
            return jsonify(json.loads(data))
        return jsonify({"error": "No current data available"}), 404

    @app.route("/api/sensor-status", methods=["GET"])
    def get_sensor_status():
        """
        Endpoint to get sensor connectivity status.
        """
        data = redis_client.get("sensor_status")
        if data:
            return jsonify(json.loads(data))
        return jsonify(
            {
                "indoor": {"ok": None, "error": "Estado desconocido"},
                "outdoor": {"ok": None, "error": "Estado desconocido"},
            }
        )

    @app.route("/api/output-status", methods=["GET"])
    def get_output_status():
        """
        Endpoint to get current output/relay status from Redis plus BCM pin mapping.
        """
        outputs = []
        for o in get_outputs():
            redis_key = o.get("redis_key")
            state = parse_bool_flag(redis_client.get(redis_key) if redis_key else None)

            override_raw = redis_client.get(manual_override_key(o.get("name", "")))
            outputs.append(
                {
                    "name": o.get("name"),
                    "label": o.get("label"),
                    "pin_bcm": o.get("pin_bcm"),
                    "redis_key": redis_key,
                    "state": state,
                    "manual_override": parse_bool_flag(override_raw),
                }
            )

        return jsonify({"outputs": outputs})

    @app.route("/api/output-control", methods=["POST"])
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
            return jsonify({"error": "Missing or invalid 'name'"}), 400
        if state is not None and not isinstance(state, bool):
            return jsonify({"error": "Invalid 'state' (must be boolean, or null to clear)"}), 400

        output = find_output(name)
        if not output:
            valid = ", ".join(o["name"] for o in get_outputs())
            return jsonify({"error": f"Unknown output name '{name}'. Valid names: {valid}"}), 404

        try:
            duration = int(duration)
        except (TypeError, ValueError):
            return jsonify({"error": "'duration_seconds' must be an integer"}), 400
        if duration < 1 or duration > MANUAL_OVERRIDE_MAX_SECONDS:
            return jsonify(
                {"error": f"'duration_seconds' must be between 1 and {MANUAL_OVERRIDE_MAX_SECONDS}"}
            ), 400

        key = manual_override_key(name)
        try:
            if state is None:
                redis_client.delete(key)
            else:
                redis_client.set(key, "true" if state else "false", ex=duration)
        except Exception as e:
            return jsonify({"error": f"Failed to publish the override: {e}"}), 500

        return jsonify(
            {
                "success": True,
                "output": {
                    "name": output.get("name"),
                    "label": output.get("label"),
                    "pin_bcm": output.get("pin_bcm"),
                    "redis_key": output.get("redis_key"),
                    "requested_state": state,
                    "expires_in_seconds": None if state is None else duration,
                },
                "note": "The control loop applies the override; it expires on its own."
                if state is not None
                else "Output handed back to automatic control.",
            }
        )

    @app.route("/api/sensor/indoor", methods=["POST"])
    def receive_indoor_sensor():
        """
        Endpoint to receive indoor sensor data from an ESP32.

        Body (JSON):
        - temperature: float (°C)
        - humidity: float (%)
        - sensor_id: str, optional. Identifies which indoor sensor posted, so
          two of them can coexist. Omitted, the reading lands on the key the
          single-sensor installation has always used, which is what keeps
          existing firmware working unchanged.

        The endpoint calculates VPD and stores data in Redis with timestamp.
        """
        data = request.get_json(silent=True) or {}
        temperature = data.get("temperature")
        humidity = data.get("humidity")
        sensor_id = data.get("sensor_id")

        # Validate required fields
        if temperature is None:
            return jsonify({"error": "Missing 'temperature' field"}), 400
        if humidity is None:
            return jsonify({"error": "Missing 'humidity' field"}), 400

        if sensor_id is not None:
            if not isinstance(sensor_id, str) or not is_valid_sensor_id(sensor_id.strip().lower()):
                # The id becomes part of a Redis key and this endpoint has no
                # authentication, so anything unexpected is refused rather than
                # sanitised.
                return jsonify({"error": f"Invalid sensor_id: {sensor_id!r}"}), 400
            sensor_id = sensor_id.strip().lower()

        # Validate types and ranges
        try:
            temperature = float(temperature)
            humidity = float(humidity)
        except (TypeError, ValueError):
            return jsonify({"error": "temperature and humidity must be numbers"}), 400

        if temperature < -40 or temperature > 80:
            return jsonify({"error": f"Invalid temperature: {temperature}°C (expected -40 to 80)"}), 400
        if humidity < 0 or humidity > 100:
            return jsonify({"error": f"Invalid humidity: {humidity}% (expected 0 to 100)"}), 400

        # Calculate VPD
        vpd = calculate_vpd(temperature, humidity)

        # Get current timestamp
        current_time = datetime.now(ARGENTINA_TZ)
        timestamp = int(current_time.timestamp())

        # Build sensor data payload
        sensor_data = {
            "temperature": round(temperature, 2),
            "humidity": round(humidity, 2),
            "vpd": vpd,
            "timestamp": timestamp,
            "datetime": current_time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "esp32",
            "sensor_id": sensor_id,
        }

        # Store in Redis
        try:
            redis_client.set(indoor_sensor_key(sensor_id), json.dumps(sensor_data))

            # Update sensor status. Identified sensors get their own entry: the
            # control loop also writes "indoor" here, and two posters fighting
            # over one entry would make the dashboard show whichever wrote last.
            sensor_status_raw = redis_client.get("sensor_status")
            if sensor_status_raw:
                sensor_status = json.loads(sensor_status_raw)
            else:
                sensor_status = {"indoor": {}, "outdoor": {}}

            sensor_status["indoor" if sensor_id is None else f"indoor:{sensor_id}"] = {
                "ok": True,
                "error": None,
                "source": "esp32",
                "last_update": current_time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            redis_client.set("sensor_status", json.dumps(sensor_status))

        except Exception as e:
            return jsonify({"error": f"Failed to store data in Redis: {e}"}), 500

        return jsonify({
            "success": True,
            "data": sensor_data,
        })

    @app.route("/api/sensor/indoor", methods=["GET"])
    def get_indoor_sensor():
        """
        Endpoint to get the latest indoor sensor data from an ESP32.

        `?sensor_id=` selects one of several; omitted, it returns the reading
        posted without an id.
        """
        sensor_id = (request.args.get("sensor_id") or "").strip().lower() or None
        if sensor_id is not None and not is_valid_sensor_id(sensor_id):
            return jsonify({"error": f"Invalid sensor_id: {sensor_id!r}"}), 400

        data = redis_client.get(indoor_sensor_key(sensor_id))
        if data:
            sensor_data = json.loads(data)
            # Check if data is stale (older than 5 minutes)
            timestamp = sensor_data.get("timestamp", 0)
            current_time = int(datetime.now(ARGENTINA_TZ).timestamp())
            age_seconds = current_time - timestamp

            sensor_data["age_seconds"] = age_seconds
            sensor_data["is_stale"] = age_seconds > 300  # 5 minutes

            return jsonify(sensor_data)
        return jsonify({"error": "No indoor sensor data available"}), 404

    @app.route("/api/sensor-history", methods=["GET"])
    def get_sensor_history():
        """
        Endpoint to get sensor history from SQLite database.
        """
        try:
            period = request.args.get("period")
            start = request.args.get("start", type=int)
            end = request.args.get("end", type=int)
            limit = request.args.get("limit", type=int)
            aggregate = request.args.get("aggregate", type=int)

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
                data = get_latest_sensor_data(limit=limit)
                return jsonify({"data": data, "count": len(data), "aggregated": False})

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
                data = _with_gaps(get_aggregated_data(start, end, aggregate), aggregate)
                return jsonify(
                    {
                        "data": data,
                        "count": len(data),
                        "start": start,
                        "end": end,
                        "aggregated": True,
                        "interval_seconds": aggregate,
                    }
                )

            # get_sensor_data_range returns newest first; the gap check needs
            # chronological order, and so do the charts.
            data = _with_gaps(
                sorted(get_sensor_data_range(start, end, limit), key=lambda r: r["timestamp"]),
                SAMPLE_INTERVAL_SECONDS,
            )
            return jsonify({"data": data, "count": len(data), "start": start, "end": end, "aggregated": False})

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/calibration", methods=["GET"])
    def list_calibration():
        """Per-sensor offsets currently applied to readings."""
        try:
            return jsonify({"calibration": get_all_calibrations()})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/calibration", methods=["POST"])
    def update_calibration():
        """
        Set a sensor's offset.

        Takes effect on the control loop's next start: it reads the offsets once
        rather than hitting the database every three seconds.
        """
        data = request.get_json(silent=True) or {}
        sensor_id = data.get("sensor_id")
        if not sensor_id or not isinstance(sensor_id, str):
            return jsonify({"error": "Missing or invalid 'sensor_id'"}), 400

        offsets = {}
        for field in ("temperature_offset", "humidity_offset"):
            value = data.get(field, 0)
            try:
                offsets[field] = float(value)
            except (TypeError, ValueError):
                return jsonify({"error": f"'{field}' must be a number"}), 400

        # A correction larger than this is a wiring or units problem, not drift.
        if abs(offsets["temperature_offset"]) > 20:
            return jsonify({"error": "'temperature_offset' fuera de rango (±20 °C)"}), 400
        if abs(offsets["humidity_offset"]) > 50:
            return jsonify({"error": "'humidity_offset' fuera de rango (±50 %)"}), 400

        if not set_calibration(sensor_id, notes=data.get("notes"), **offsets):
            return jsonify({"error": "Failed to store calibration"}), 500

        return jsonify({
            "success": True,
            "sensor_id": sensor_id,
            **offsets,
            "note": "Se aplica cuando reinicie el loop de control.",
        })

    @app.route("/api/database-stats", methods=["GET"])
    def database_stats():
        try:
            stats = get_database_stats()
            return jsonify(stats)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/period-summary", methods=["GET"])
    def period_summary():
        """
        Get summary statistics (avg, min, max) for a time period.
        Query params: hours (float) or days (int), grow_id (optional)
        """
        try:
            hours = request.args.get("hours", type=float)
            days = request.args.get("days", type=int)
            grow_id = request.args.get("grow_id", type=int)

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
            return jsonify(summary)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/history/aggregated", methods=["GET"])
    def get_history_aggregated():
        try:
            days = request.args.get("days", default=7, type=int)
            interval = request.args.get("interval", default="hourly", type=str)
            grow_id = request.args.get("grow_id", type=int)

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
                    "grow_id": grow_id,
                    "start_datetime": datetime.fromtimestamp(start_timestamp, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                    "end_datetime": datetime.fromtimestamp(end_timestamp, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ===============================
    # Grow Management Endpoints
    # ===============================

    @app.route("/api/grows", methods=["GET"])
    def list_grows():
        try:
            grows = get_all_grows()
            return jsonify({"grows": grows, "count": len(grows)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/grows/active", methods=["GET"])
    def active_grow():
        try:
            grow = get_active_grow()
            if grow:
                return jsonify(grow)
            return jsonify({"error": "No active grow found"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/grows", methods=["POST"])
    def create_new_grow():
        try:
            data = request.get_json()
            if not data or "name" not in data:
                return jsonify({"error": "Name is required"}), 400

            name = data["name"]
            stage = data.get("stage", "early_veg")
            notes = data.get("notes", "")

            valid_stages = ["early_veg", "late_veg", "flowering", "dry"]
            if stage not in valid_stages:
                return jsonify({"error": f'Invalid stage. Use one of: {", ".join(valid_stages)}'}), 400

            grow_id = create_grow(name, stage, notes)
            if grow_id:
                return jsonify({"success": True, "grow_id": grow_id, "message": f'Grow "{name}" created successfully'}), 201
            return jsonify({"error": "Failed to create grow"}), 500

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/grows/<int:grow_id>/end", methods=["POST"])
    def finish_grow(grow_id: int):
        try:
            success = end_grow(grow_id)
            if success:
                return jsonify({"success": True, "message": f"Grow {grow_id} ended successfully"})
            return jsonify({"error": "Failed to end grow"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/grows/<int:grow_id>/activate", methods=["POST"])
    def activate_grow_endpoint(grow_id: int):
        try:
            success = set_active_grow(grow_id)
            if success:
                return jsonify({"success": True, "message": f"Grow {grow_id} activated successfully"})
            return jsonify({"error": "Failed to activate grow"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/grows/<int:grow_id>/timeline", methods=["GET"])
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

    @app.route("/api/grows/<int:grow_id>/stage", methods=["PUT"])
    def update_stage_endpoint(grow_id: int):
        try:
            data = request.get_json()
            if not data or "stage" not in data:
                return jsonify({"error": "Stage is required"}), 400

            stage = data["stage"]
            valid_stages = ["early_veg", "late_veg", "flowering", "dry"]
            if stage not in valid_stages:
                return jsonify({"error": f'Invalid stage. Use one of: {", ".join(valid_stages)}'}), 400

            success = update_grow_stage(grow_id, stage, notes=data.get("notes"))
            if success:
                return jsonify({"success": True, "message": f"Grow {grow_id} stage updated to {stage}"})
            return jsonify({"error": "Failed to update stage"}), 500

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ===============================
    # Analytics Endpoints
    # ===============================

    @app.route("/api/vpd-score", methods=["GET"])
    def vpd_score_endpoint():
        """
        Get VPD score (% of time in optimal range).
        Query params: days (int, default 7), grow_id (optional)
        """
        try:
            days = request.args.get("days", default=7, type=int)
            grow_id = request.args.get("grow_id", type=int)

            score = get_vpd_score(days=days, grow_id=grow_id)
            return jsonify(score)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/weekly-report", methods=["GET"])
    def weekly_report_endpoint():
        """
        Get comprehensive weekly report.
        Query params: grow_id (optional)
        """
        try:
            grow_id = request.args.get("grow_id", type=int)

            report = get_weekly_report(grow_id=grow_id)
            return jsonify(report)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/anomalies", methods=["GET"])
    def anomalies_endpoint():
        """
        Detect anomalies in sensor data.
        Query params: hours (int, default 24), grow_id (optional)
        """
        try:
            hours = request.args.get("hours", default=24, type=int)
            grow_id = request.args.get("grow_id", type=int)

            anomalies = detect_anomalies(hours=hours, grow_id=grow_id)
            return jsonify(anomalies)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return app


# Convenience for WSGI servers
app = create_app()

