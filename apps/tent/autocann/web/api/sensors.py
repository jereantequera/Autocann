"""
Indoor sensors: what the nodes post, what the dashboard reads, and their calibration.
"""

from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, jsonify, request

from autocann.config import (
    INDOOR_SENSOR_MAX_AGE_SECONDS,
    PRIMARY,
    WITNESS,
    indoor_sensor_key,
    indoor_sensors_from_env,
    is_valid_sensor_id,
)
from autocann.control.plausible import HUMIDITY_MAX_PERCENT, HUMIDITY_MIN_PERCENT, TEMPERATURE_MAX_C, TEMPERATURE_MIN_C
from autocann.control.vpd_math import (
    calculate_vpd,
)
from autocann.db import (
    get_all_calibrations,
    set_calibration,
)
from autocann.time import ARGENTINA_TZ
from autocann.web.deps import get_redis

bp = Blueprint("sensors", __name__)


@bp.route("/api/sensor-status", methods=["GET"])
def get_sensor_status():
    """
    Endpoint to get sensor connectivity status.
    """
    data = get_redis().get("sensor_status")
    if data:
        return jsonify(json.loads(data))
    return jsonify(
        {
            "indoor": {"ok": None, "error": "Estado desconocido"},
            "outdoor": {"ok": None, "error": "Estado desconocido"},
        }
    )


def _indoor_delta(readings):
    """
    Witness minus primary, the direction the drift monitor measures in.

    Only computed for two fresh readings. Subtracting a stale number from a live
    one yields a difference that is about the clock, not about the sensors, and
    on a dashboard that difference reads as a fault in the pair.
    """
    primary = readings.get(PRIMARY)
    witness = readings.get(WITNESS)
    if primary is None or witness is None:
        return None
    if primary["is_stale"] or witness["is_stale"]:
        return None
    values = (primary["temperature"], witness["temperature"],
              primary["humidity"], witness["humidity"])
    if any(value is None for value in values):
        return None
    return {
        "temperature": round(witness["temperature"] - primary["temperature"], 2),
        "humidity": round(witness["humidity"] - primary["humidity"], 2),
    }


@bp.route("/api/sensor/indoor", methods=["POST"])
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

    # Physically impossible is a failed read: refused, so it is never shown,
    # used or stored (the node logs the 400). See control/plausible.py.
    if not TEMPERATURE_MIN_C <= temperature <= TEMPERATURE_MAX_C:
        return jsonify({"error": f"Invalid temperature: {temperature}°C "
                                 f"(expected {TEMPERATURE_MIN_C:g} to {TEMPERATURE_MAX_C:g})"}), 400
    if not HUMIDITY_MIN_PERCENT <= humidity <= HUMIDITY_MAX_PERCENT:
        return jsonify({"error": f"Invalid humidity: {humidity}% (expected 0 to 100)"}), 400
    vpd = calculate_vpd(temperature, humidity)

    current_time = datetime.now(ARGENTINA_TZ)
    timestamp = int(current_time.timestamp())

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
        get_redis().set(indoor_sensor_key(sensor_id), json.dumps(sensor_data))

        # Update sensor status. Identified sensors get their own entry: the
        # control loop also writes "indoor" here, and two posters fighting
        # over one entry would make the dashboard show whichever wrote last.
        sensor_status_raw = get_redis().get("sensor_status")
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
        get_redis().set("sensor_status", json.dumps(sensor_status))

    except Exception as e:
        return jsonify({"error": f"Failed to store data in Redis: {e}"}), 500

    return jsonify({
        "success": True,
        "data": sensor_data,
    })


@bp.route("/api/sensor/indoor", methods=["GET"])
def get_indoor_sensor():
    """
    Endpoint to get the latest indoor sensor data from an ESP32.

    `?sensor_id=` selects one of several; omitted, it returns the reading
    posted without an id.
    """
    sensor_id = (request.args.get("sensor_id") or "").strip().lower() or None
    if sensor_id is not None and not is_valid_sensor_id(sensor_id):
        return jsonify({"error": f"Invalid sensor_id: {sensor_id!r}"}), 400

    data = get_redis().get(indoor_sensor_key(sensor_id))
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


@bp.route("/api/sensors/indoor", methods=["GET"])
def get_indoor_sensors():
    """
    Every indoor sensor at once, each with its role and the age of its reading.

    The dashboard cannot assemble this from `/api/sensor/indoor?sensor_id=`:
    which ids are installed, and which of them drives control, is
    server-side configuration (AUTOCANN_INDOOR_PRIMARY /
    AUTOCANN_INDOOR_WITNESS). So the list is served rather than guessed, and
    a single-sensor installation answers with one entry rather than being a
    special case in the frontend.

    The readings are raw — exactly what the node posted. The hero cards show
    the primary *after* the median filter and the calibration offsets, so
    the two can differ by a couple of tenths and both be right.
    """
    try:
        sensors = indoor_sensors_from_env()
    except ValueError as e:
        # Answering with a plausible single-sensor list would hide the
        # second sensor for good, which is the one thing this endpoint
        # exists to prevent.
        return jsonify({"error": f"Configuración de sensores inválida: {e}"}), 500

    now = int(datetime.now(ARGENTINA_TZ).timestamp())
    entries = []
    by_role = {}
    try:
        for role, sensor_id, key in sensors.entries():
            raw = get_redis().get(key)
            payload = json.loads(raw) if raw else None
            entry = {
                "role": role,
                "sensor_id": sensor_id,
                "redis_key": key,
                "temperature": None,
                "humidity": None,
                "vpd": None,
                "timestamp": None,
                "datetime": None,
                "age_seconds": None,
                # A sensor that has never posted is not fresh. Defaulting
                # the other way would paint an empty card green.
                "is_stale": True,
                "has_reading": False,
            }
            if payload:
                age = now - int(payload.get("timestamp") or 0)
                entry.update(
                    {
                        "temperature": payload.get("temperature"),
                        "humidity": payload.get("humidity"),
                        "vpd": payload.get("vpd"),
                        "timestamp": payload.get("timestamp"),
                        "datetime": payload.get("datetime"),
                        "age_seconds": age,
                        "is_stale": age > INDOOR_SENSOR_MAX_AGE_SECONDS,
                        "has_reading": True,
                    }
                )
            entries.append(entry)
            by_role[role] = entry

        drift = None
        status_raw = get_redis().get("sensor_status")
        if status_raw:
            drift = (json.loads(status_raw) or {}).get("drift")
    except Exception as e:
        return jsonify({"error": f"Failed to read the indoor sensors: {e}"}), 500

    # The drift report's per-sensor flags are keyed by the same role names,
    # so each card can carry its own verdict instead of one shared alarm.
    flags = (drift or {}).get("flags") or {}
    for entry in entries:
        entry["flags"] = list(flags.get(entry["role"]) or [])

    return jsonify(
        {
            "sensors": entries,
            "delta": _indoor_delta(by_role),
            "drift": drift,
            # Whether a cross-check exists at all. The dashboard cannot
            # infer it from the number of sensors: an installation can have
            # several and still have nothing to compare, because an observer
            # is not a witness.
            "has_witness": sensors.has_witness,
            "max_age_seconds": INDOOR_SENSOR_MAX_AGE_SECONDS,
        }
    )


@bp.route("/api/calibration", methods=["GET"])
def list_calibration():
    """Per-sensor offsets currently applied to readings."""
    try:
        return jsonify({"calibration": get_all_calibrations()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/calibration", methods=["POST"])
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
