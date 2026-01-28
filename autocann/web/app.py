from __future__ import annotations

import json
from datetime import datetime

import pytz
import redis
from flask import Flask, jsonify, render_template, request

from autocann.config import redis_config_from_env
from autocann.control.vpd_math import calculate_vpd
from autocann.db import (create_grow, detect_anomalies, end_grow,
                         get_active_grow, get_aggregated_data, get_all_grows,
                         get_database_stats, get_latest_sensor_data,
                         get_period_summary, get_sensor_data_range,
                         get_vpd_score, get_weekly_report, set_active_grow,
                         update_grow_stage)
from autocann.hardware.outputs import OUTPUTS
from autocann.paths import TEMPLATES_DIR
from autocann.time import ARGENTINA_TZ


def create_app() -> Flask:
    app = Flask(__name__, template_folder=str(TEMPLATES_DIR))
    redis_cfg = redis_config_from_env()
    redis_client = redis.Redis(host=redis_cfg.host, port=redis_cfg.port, db=redis_cfg.db)

    @app.route("/")
    def index():
        """
        Serve the main dashboard page.
        """
        return render_template("index.html")

    @app.route("/api/historical-data", methods=["GET"])
    def get_historical_data():
        """
        Endpoint to get all historical data from Redis.
        Returns data for all time windows (6h, 12h, 24h, 1w).
        """
        time_windows = ["6h", "12h", "24h", "1w"]
        response_data = {}

        for window in time_windows:
            key = f"historical_data_{window}"
            data = redis_client.get(key)
            if data:
                response_data[window] = json.loads(data)
            else:
                response_data[window] = []

        return jsonify(response_data)

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
        for o in OUTPUTS:
            redis_key = o.get("redis_key")
            raw = redis_client.get(redis_key) if redis_key else None
            if raw is None:
                state = None
            else:
                try:
                    val = raw.decode("utf-8").strip().lower()
                except Exception:
                    val = str(raw).strip().lower()
                if val in ("true", "1", "on", "yes"):
                    state = True
                elif val in ("false", "0", "off", "no"):
                    state = False
                else:
                    state = None

            outputs.append(
                {
                    "name": o.get("name"),
                    "label": o.get("label"),
                    "pin_bcm": o.get("pin_bcm"),
                    "redis_key": redis_key,
                    "state": state,
                }
            )

        return jsonify({"outputs": outputs})

    @app.route("/api/output-control", methods=["POST"])
    def set_output_control():
        """
        Endpoint to manually turn an output/relay ON/OFF.

        Body (JSON):
        - name: output name (one of autocann.hardware.outputs.OUTPUTS[*].name)
        - state: boolean (true=on, false=off)
        """
        data = request.get_json(silent=True) or {}
        name = data.get("name")
        state = data.get("state")

        if not name or not isinstance(name, str):
            return jsonify({"error": "Missing or invalid 'name'"}), 400
        if not isinstance(state, bool):
            return jsonify({"error": "Missing or invalid 'state' (must be boolean)"}), 400

        output = None
        for o in OUTPUTS:
            if o.get("name") == name:
                output = o
                break

        if not output:
            return jsonify({"error": f"Unknown output name '{name}'"}), 404

        pin_bcm = output.get("pin_bcm")
        redis_key = output.get("redis_key")
        active_high = bool(output.get("active_high", True))

        if pin_bcm is None:
            return jsonify({"error": f"Output '{name}' has no pin configured"}), 500

        # 1) Try to control the real GPIO (on Raspberry Pi).
        try:
            import gpiozero  # type: ignore
        except Exception:
            return (
                jsonify(
                    {
                        "error": "gpiozero is not available on this machine. "
                        "This endpoint must run on the Raspberry Pi with '--extra rpi' deps installed."
                    }
                ),
                501,
            )

        try:
            dev = gpiozero.OutputDevice(int(pin_bcm), active_high=active_high, initial_value=False)
            if state:
                dev.on()
            else:
                dev.off()
            dev.close()
        except Exception as e:
            return jsonify({"error": f"Failed to control GPIO BCM {pin_bcm}: {e}"}), 500

        # 2) Mirror state in Redis so the dashboard can show it.
        try:
            if redis_key:
                redis_client.set(redis_key, "true" if state else "false")
        except Exception as e:
            return jsonify({"error": f"GPIO set, but failed updating Redis: {e}"}), 500

        return jsonify(
            {
                "success": True,
                "output": {
                    "name": output.get("name"),
                    "label": output.get("label"),
                    "pin_bcm": pin_bcm,
                    "redis_key": redis_key,
                    "state": state,
                },
            }
        )

    @app.route("/api/sensor/indoor", methods=["POST"])
    def receive_indoor_sensor():
        """
        Endpoint to receive indoor sensor data from ESP32.

        Body (JSON):
        - temperature: float (°C)
        - humidity: float (%)

        The endpoint calculates VPD and stores data in Redis with timestamp.
        """
        data = request.get_json(silent=True) or {}
        temperature = data.get("temperature")
        humidity = data.get("humidity")

        # Validate required fields
        if temperature is None:
            return jsonify({"error": "Missing 'temperature' field"}), 400
        if humidity is None:
            return jsonify({"error": "Missing 'humidity' field"}), 400

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
        }

        # Store in Redis
        try:
            redis_client.set("esp32_indoor", json.dumps(sensor_data))

            # Update sensor status
            sensor_status_raw = redis_client.get("sensor_status")
            if sensor_status_raw:
                sensor_status = json.loads(sensor_status_raw)
            else:
                sensor_status = {"indoor": {}, "outdoor": {}}

            sensor_status["indoor"] = {
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
        Endpoint to get the latest indoor sensor data from ESP32.
        """
        data = redis_client.get("esp32_indoor")
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

            if aggregate:
                data = get_aggregated_data(start, end, aggregate)
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

            data = get_sensor_data_range(start, end, limit)
            return jsonify({"data": data, "count": len(data), "start": start, "end": end, "aggregated": False})

        except Exception as e:
            return jsonify({"error": str(e)}), 500

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

            data = get_aggregated_data(start_timestamp, end_timestamp, interval_seconds, grow_id)

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

            success = update_grow_stage(grow_id, stage)
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

