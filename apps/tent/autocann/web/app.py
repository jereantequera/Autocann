from __future__ import annotations

import json
from datetime import datetime

from flask import Flask, jsonify, render_template, request
from markupsafe import Markup, escape

from autocann import __version__
from autocann.config import (
    CONTROL_MODES,
    DESIRED_OUTPUTS_KEY,
    DESIRED_OUTPUTS_MAX_AGE_SECONDS,
    INDOOR_SENSOR_MAX_AGE_SECONDS,
    MODE_ACTIVE,
    MODE_PAUSED,
    PRIMARY,
    TEST_MODE_KEY,
    TEST_MODE_OUTPUTS_KEY,
    TEST_MODE_SETTING,
    UNCONTROLLED_MODES,
    WITNESS,
    indoor_sensor_key,
    indoor_sensors_from_env,
    is_valid_sensor_id,
    redis_config_from_env,
)
from autocann.control.feeding import (
    chart_columns,
    effective_week,
    plan_for_week,
    scale_to_volume,
    water_only_reason,
    weeks_in_stage,
)
from autocann.control.quiet import ACTION_OUTPUTS, QuietWindow, parse_hhmm, windows_from_dict
from autocann.control.sampling import insert_gaps
from autocann.control.vpd_math import (
    LEAF_TEMP_OFFSET_C,
    STAGE_EXPECTED_DAYS,
    STAGE_NAMES,
    STAGES,
    VPD_RANGES,
    calculate_vpd,
    humidity_range_bounds_for_stage,
    humidity_range_for_stage,
)
from autocann.db import (
    ALL_GROWS,
    create_grow,
    delete_irrigation,
    detect_anomalies,
    end_grow,
    get_active_grow,
    get_aggregated_data,
    get_all_calibrations,
    get_all_grows,
    get_database_stats,
    get_feed_plan,
    get_irrigations,
    get_latest_sensor_data,
    get_logbook,
    get_period_summary,
    get_sensor_data_range,
    get_setting,
    get_stage_timeline,
    get_vpd_score,
    get_weekly_report,
    last_irrigation_litres,
    record_irrigation,
    replace_feed_plan,
    set_active_grow,
    set_calibration,
    set_setting,
    update_grow_stage,
)
from autocann.hardware.outputs import (
    find_output,
    get_outputs,
    manual_override_key,
    output_names,
    parse_bool_flag,
    pin_sources,
)
from autocann.paths import STATIC_DIR, TEMPLATES_DIR
from autocann.redis_pool import pooled_client
from autocann.time import ARGENTINA_TZ
from autocann.web.auth import register_auth
from autocann.web.diagnostics import (
    health_snapshot,
    reframe_history_anomalies,
    register_diagnostics,
)

#: Default and maximum lifetime of a manual output override.
MANUAL_OVERRIDE_DEFAULT_SECONDS = 15 * 60
MANUAL_OVERRIDE_MAX_SECONDS = 2 * 60 * 60


#: How often the control loop writes a sample. Used to tell a real outage from
#: the normal spacing between points.
SAMPLE_INTERVAL_SECONDS = 300

#: Air temperature span of the VPD chart. Wide enough to show every band the
#: dashboard can draw; 0.5 °C steps match the resolution of the sensors.
BAND_TEMP_MIN = 18.0
BAND_TEMP_MAX = 32.0
BAND_TEMP_STEP = 0.5

#: Logbook window and size, in days and entries. Anything outside these is clamped
#: rather than refused: the dashboard asks for "a bit more" and should still get it.
LOGBOOK_DAYS_DEFAULT = 7
LOGBOOK_DAYS_MAX = 90
LOGBOOK_LIMIT_DEFAULT = 80
LOGBOOK_LIMIT_MAX = 500

#: Spanish article and noun for each relay, for the logbook sentences.
RELAY_LABELS = {
    "humidity_up": ("el", "humidificador"),
    "humidity_down": ("el", "deshumidificador"),
    "ventilation": ("la", "extracción"),
}

#: Control-loop mode changes, which the logbook files under "sistema".
CONTROL_MODE_TEXT = {
    "pause": "Se pausó el control",
    "monitor": "Pasó a monitoreo: se registra, pero no se controla",
    "resume": "Volvió el control automático",
}


def _es_number(value: float, decimals: int = 0) -> str:
    """A number with a decimal comma, as the dashboard writes them."""
    return f"{value:.{decimals}f}".replace(".", ",")


def _es_amount(value: float) -> str:
    """Litres and doses: no decimals when they are whole, one otherwise."""
    return _es_number(value) if float(value).is_integer() else _es_number(value, 1)


def _local_datetime(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _logbook_entry(entry: dict, grow_name: str) -> dict:
    """
    One logbook row, as the dashboard shows it: a kind, a sentence and a detail.

    Unknown relay events do not crash the endpoint: a new event_type written by
    a newer loop still shows up, as a readable line, until someone maps it.
    """
    source = entry["source"]
    if source == "control":
        event_type, value = entry["event_type"], entry["value"]
        if event_type in RELAY_LABELS and value in ("on", "off"):
            article, noun = RELAY_LABELS[event_type]
            verb = "Se prendió" if value == "on" else "Se apagó"
            kind, text, detail = "salida", f"{verb} {article} {noun}", ""
        elif event_type in CONTROL_MODE_TEXT:
            kind, text, detail = "sistema", CONTROL_MODE_TEXT[event_type], ""
        else:
            kind, text, detail = "sistema", f"Evento de control: {event_type} {value}", ""
    elif source == "irrigation":
        kind = "riego"
        text = f"Riego · {_es_amount(entry['litres'])} L"
        parts = [
            f"{item['name']} {_es_amount(item['dose'])} {item['unit']}"
            for item in entry["items"]
        ] or ["Solo agua"]
        if entry["ph"] is not None:
            parts.append(f"pH {_es_number(entry['ph'], 1)}")
        if entry["ec"] is not None:
            parts.append(f"EC {_es_number(entry['ec'], 1)}")
        detail = " · ".join(parts)
    else:
        kind = "cultivo"
        stage_name = STAGE_NAMES.get(entry["stage"], entry["stage"])
        if entry["is_start"]:
            text = f"Empezó {grow_name} en {stage_name}"
        else:
            text = f"Pasó a {stage_name}"
        detail = ""

    return {
        "timestamp": entry["timestamp"],
        "datetime": _local_datetime(entry["timestamp"]),
        "kind": kind,
        "text": text,
        "detail": detail,
    }


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


def _requested_grow():
    """
    The `grow_id` query parameter, in the form the database layer wants.

    A string rather than `type=int` so that `grow_id=all` can be asked for at
    all: an integer has no spelling for "every grow", and leaving the parameter
    out has to keep meaning "whatever grow is active", which is what the
    dashboard is about.

    Raises ValueError on anything else, so a typo is a 400 rather than a
    silent fall back to a different scope than the caller asked for.
    """
    raw = (request.args.get("grow_id") or "").strip().lower()
    if not raw:
        return None
    if raw == ALL_GROWS:
        return ALL_GROWS
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"'grow_id' debe ser un número o '{ALL_GROWS}', no {raw!r}") from None


def _grow_scope(grow_id):
    """
    How a response says which grow it is about.

    Served on every scoped endpoint. The dashboard cannot infer it: the default
    is resolved server-side, so a chart that does not ask gets the active grow
    and has no way to label it — and a chart whose meaning changes when
    somebody activates another grow, without saying so, is worse than one with
    no label at all.
    """
    if grow_id == ALL_GROWS:
        return {"grow_id": None, "grow_scope": ALL_GROWS}
    if grow_id is not None:
        return {"grow_id": int(grow_id), "grow_scope": "grow"}
    active = get_active_grow()
    return {
        "grow_id": int(active["id"]) if active else None,
        "grow_name": active["name"] if active else None,
        "grow_scope": "active" if active else "none",
    }


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



def static_asset_version() -> str:
    """
    A cache-busting token that changes when the files behind it do.

    It used to be `__version__`, which has read "0.1.0" since the first commit
    and has never been bumped once. Every stylesheet and every module shipped
    since then went out behind the same query string, so the promise in
    index.html - that a deploy busts the browser cache - had never been kept:
    the tablet on the tent wall kept its old app.css against freshly deployed
    markup, which is worse than no cache-busting, because it looks like the
    deploy broke the page.

    The newest mtime under static/ answers it exactly, and without anyone
    having to remember anything: `git pull` stamps every file it rewrites, so
    the token moves on the deploys that changed an asset and stays put on the
    ones that did not. The version stays in front of it so the string is still
    recognisable as this app's.
    """
    try:
        newest = max(
            (path.stat().st_mtime for path in STATIC_DIR.rglob("*") if path.is_file()),
            default=0.0,
        )
    except OSError:
        # An unreadable static dir is a problem, but not this function's to
        # report: serving a stale token is better than failing to build the app.
        return __version__
    return f"{__version__}-{int(newest):x}" if newest else __version__

def icon(name: str, extra_class: str = "") -> Markup:
    """
    An icon from the sprite in partials/icons.svg, for the templates.

    The same markup icon() in static/js/util.js builds, so an icon rendered by
    the server and one rendered by a poller look the same.
    """
    classes = f"icon {extra_class}".strip()
    return Markup(
        f'<svg class="{escape(classes)}" aria-hidden="true" focusable="false">'
        f'<use href="#i-{escape(name)}"></use></svg>'
    )


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(TEMPLATES_DIR),
        static_folder=str(STATIC_DIR),
        static_url_path="/static",
    )

    # Computed once, here: the services restart on every deploy, so there is
    # nothing a per-request stat would catch that this misses, and walking
    # static/ on every page load would be a directory scan per visit.
    asset_version = static_asset_version()

    @app.context_processor
    def inject_version():
        # Appended to static URLs so a deploy busts the browser cache without
        # having to serve the assets with no-cache.
        return {"asset_version": asset_version, "icon": icon}
    redis_cfg = redis_config_from_env()
    # One client for the life of the process, with a bounded pool and a socket
    # that gives up: see autocann/redis_pool.py for what the defaults cost.
    redis_client = pooled_client(redis_cfg)

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
                "stage_names": STAGE_NAMES,
                # Grounds the stage proportions bar in Cultivo. The page used to
                # read it from this payload and get nothing, so the bar had no
                # expected lengths to draw against.
                "stage_expected_days": STAGE_EXPECTED_DAYS,
            }
        )

    @app.route("/api/vpd-band", methods=["GET"])
    def get_vpd_band():
        """
        The humidity band a stage allows, at each air temperature of the chart.

        Computed here, with the controller's own functions and leaf offset, and
        not in the browser: a chart that re-implemented the Tetens math would
        drift from what the loop actually does, and the operator would be
        reading a band the tent is not held to. Each point is what
        `resolve_setpoint` gets for that air temperature.
        """
        stage = (request.args.get("stage") or "").strip()
        if not stage:
            grow = get_active_grow()
            if not grow:
                return jsonify({"error": "No active grow found"}), 404
            stage = grow["stage"]
        if stage not in STAGES:
            return jsonify({"error": f'Invalid stage. Use one of: {", ".join(STAGES)}'}), 400

        explicit = humidity_range_for_stage(stage)
        point_count = round((BAND_TEMP_MAX - BAND_TEMP_MIN) / BAND_TEMP_STEP) + 1
        points = []
        for step in range(point_count):
            temperature = round(BAND_TEMP_MIN + step * BAND_TEMP_STEP, 1)
            if explicit is not None:
                low, high = explicit
            else:
                low, high = sorted(
                    humidity_range_bounds_for_stage(
                        stage, temperature, round(temperature - LEAF_TEMP_OFFSET_C, 1)
                    )
                )
            points.append({
                "temperature": temperature,
                "humidity_low": round(low, 1),
                "humidity_high": round(high, 1),
            })

        vpd_min = vpd_max = None
        if explicit is None:
            vpd_min, vpd_max = VPD_RANGES[stage]

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

    def _control_mode():
        """
        Which of the three states the tent is in, as (mode, since).

        The database is the truth and Redis is the mirror, so a Redis that was
        restarted or flushed does not read as "not paused" and quietly hand the
        tent back to the controller. Redis is still answered from first because
        it is the common case and costs nothing.

        A bare timestamp in Redis predates monitoring, and the only safe
        reading of it is the one it always had: a pause.
        """
        raw = None
        try:
            raw = redis_client.get(TEST_MODE_KEY)
        except Exception:
            raw = None

        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            try:
                return MODE_PAUSED, int(raw)
            except (TypeError, ValueError):
                pass
            try:
                mirrored = json.loads(raw)
            except (TypeError, ValueError):
                mirrored = None
            if isinstance(mirrored, dict):
                mode = mirrored.get("mode")
                if mode not in UNCONTROLLED_MODES:
                    mode = MODE_PAUSED
                try:
                    return mode, int(mirrored.get("since"))
                except (TypeError, ValueError):
                    return mode, int(datetime.now(ARGENTINA_TZ).timestamp())

        stored = get_setting(TEST_MODE_SETTING, None)
        if not isinstance(stored, dict) or not stored.get("active"):
            return MODE_ACTIVE, None

        mode = stored.get("mode")
        if mode not in UNCONTROLLED_MODES:
            mode = MODE_PAUSED

        since = stored.get("since")
        try:
            since = int(since)
        except (TypeError, ValueError):
            since = int(datetime.now(ARGENTINA_TZ).timestamp())

        # Seen in the database but not in Redis: put the mirror back, so the
        # control loop - which only reads Redis each cycle - sees it too.
        try:
            redis_client.set(TEST_MODE_KEY, json.dumps({"mode": mode, "since": since}))
        except Exception:
            pass
        return mode, since

    def _test_mode_payload():
        mode, since = _control_mode()
        try:
            raw = redis_client.get(TEST_MODE_OUTPUTS_KEY)
            requested = json.loads(raw) if raw else {}
        except Exception:
            requested = {}
        if not isinstance(requested, dict):
            requested = {}

        return {
            # Kept, and kept meaning the same thing: "the loop is not in
            # charge". Every caller that only ever asked that question - the
            # relay buttons, the chip's disabled state - keeps working without
            # learning about the third state.
            "active": since is not None,
            "mode": mode,
            "since": since,
            # How long it has been, not how long is left: there is no deadline
            # to count down to any more, and the number that matters is how long
            # the tent has been going unattended.
            "elapsed_seconds": (
                None if since is None
                else max(0, int(datetime.now(ARGENTINA_TZ).timestamp()) - since)
            ),
            "requested_outputs": {
                name: bool(requested.get(name, False)) for name in output_names()
            },
        }

    @app.route("/api/output-status", methods=["GET"])
    def get_output_status():
        """
        Endpoint to get current output/relay status from Redis plus, per output,
        the board and pin that actually switch it.

        Carries the test-mode state as well. The dashboard draws both from this
        one answer so they cannot disagree: two polls could show manual buttons
        over outputs the loop is already deciding again, which is the one
        confusion worth designing out.
        """
        # Published by the control loop: what it refused to switch yet, and for
        # how long, so a relay that does not move can say why.
        held = {}
        try:
            raw = redis_client.get("sensors")
            if raw:
                held = (json.loads(raw) or {}).get("held_outputs") or {}
        except Exception:
            held = {}

        # Resolved once, not per output: it reads the environment.
        sources = pin_sources()

        outputs = []
        for o in get_outputs():
            redis_key = o.get("redis_key")
            state = parse_bool_flag(redis_client.get(redis_key) if redis_key else None)

            override_raw = redis_client.get(manual_override_key(o.get("name", "")))
            outputs.append(
                {
                    "name": o.get("name"),
                    "label": o.get("label"),
                    "pin": sources.get(o.get("name")),
                    "redis_key": redis_key,
                    "state": state,
                    "manual_override": parse_bool_flag(override_raw),
                    "held_seconds": held.get(o.get("name")),
                }
            )

        try:
            test_mode = _test_mode_payload()
        except Exception:
            test_mode = {"active": False}

        return jsonify({"outputs": outputs, "test_mode": test_mode})

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
                    "pin": pin_sources().get(name),
                    "redis_key": output.get("redis_key"),
                    "requested_state": state,
                    "expires_in_seconds": None if state is None else duration,
                },
                "note": "The control loop applies the override; it expires on its own."
                if state is not None
                else "Output handed back to automatic control.",
            }
        )

    @app.route("/api/outputs/desired", methods=["GET"])
    def get_desired_outputs():
        """
        The full desired output state, for a remote relay node to poll.

        Every output is always named, so the node never has to merge a partial
        update against its own idea of the current state.

        When the control loop's last write is too old, the answer is everything
        off with `failsafe` set. A node that kept applying a stale command would
        leave a humidifier running long after the loop died, and the loop's own
        failsafe cannot help once the loop is what stopped.
        """
        off = {name: False for name in output_names()}

        def failsafe(reason):
            return jsonify({
                "outputs": off,
                "failsafe": True,
                "reason": reason,
                "max_age_seconds": DESIRED_OUTPUTS_MAX_AGE_SECONDS,
            })

        try:
            raw = redis_client.get(DESIRED_OUTPUTS_KEY)
        except Exception as e:
            return failsafe(f"redis unavailable: {e}")
        if not raw:
            return failsafe("no desired state published")

        try:
            payload = json.loads(raw)
            outputs = payload["outputs"]
            age = int(datetime.now(ARGENTINA_TZ).timestamp()) - int(payload["ts"])
        except Exception as e:
            return failsafe(f"unreadable desired state: {e}")

        if age > DESIRED_OUTPUTS_MAX_AGE_SECONDS:
            return failsafe(f"stale desired state ({age}s old)")

        # Names the loop did not publish default to off rather than being
        # omitted: a node must never have to guess what to do with an output.
        return jsonify({
            "outputs": {name: bool(outputs.get(name, False)) for name in off},
            "failsafe": False,
            "age_seconds": age,
            "max_age_seconds": DESIRED_OUTPUTS_MAX_AGE_SECONDS,
        })

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

    @app.route("/api/sensors/indoor", methods=["GET"])
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
                raw = redis_client.get(key)
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
            status_raw = redis_client.get("sensor_status")
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

    # ===============================
    # Test mode
    # ===============================

    @app.route("/api/test-mode", methods=["GET"])
    def get_test_mode():
        """
        Whether the control loop is standing down, and since when.
        """
        try:
            return jsonify(_test_mode_payload())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/test-mode", methods=["POST"])
    def set_test_mode():
        """
        Put the tent in one of its three states.

        Body (JSON), either:
        - mode: "active" | "monitor" | "paused"
        - enabled: boolean  (the older spelling; true means "paused")

        `enabled` is still accepted because it is what every build before
        monitoring sent, and a dashboard cached in a browser on the tent wall
        goes on sending it for as long as that tab stays open.

        The three states exist because "no decidas" and "no guardes" turned out
        not to be the same request. Pausing is for working on the installation:
        nothing decides, nothing is recorded, and the relays follow what is
        asked by hand. Monitoring is for a tent that is set up but not planted:
        nothing decides and nothing switches, and every reading is recorded,
        because what the room does on its own is the baseline worth having
        before there is anything alive in there to protect.

        Neither ends on its own. The pause used to expire after eight hours;
        what that bought - never being left uncontrolled by accident - is now
        bought by saying so in the header on every view instead, and what it
        cost was relays closing again at a moment nobody chose.

        Written to the database first, then mirrored into Redis. The loop reads
        Redis, so the order matters only when one of the two writes fails: a
        state recorded and not mirrored is noticed and repaired on the next
        read, while one mirrored and not recorded would vanish on the next
        reboot with nothing to notice it.
        """
        data = request.get_json(silent=True) or {}

        mode = data.get("mode")
        if mode is None:
            enabled = data.get("enabled")
            if not isinstance(enabled, bool):
                return jsonify({
                    "error": "Missing or invalid 'mode' "
                             f"(one of: {', '.join(CONTROL_MODES)})"
                }), 400
            mode = MODE_PAUSED if enabled else MODE_ACTIVE
        if mode not in CONTROL_MODES:
            return jsonify({
                "error": f"Unknown mode '{mode}'. Valid modes: {', '.join(CONTROL_MODES)}"
            }), 400

        if mode == MODE_ACTIVE:
            if not set_setting(TEST_MODE_SETTING, {"active": False, "mode": MODE_ACTIVE}):
                return jsonify({"error": "No se pudo devolver el control al lazo"}), 500
            try:
                redis_client.delete(TEST_MODE_KEY)
                # Cleared together: a later pause must not begin by energising
                # whatever was left switched on in this one.
                redis_client.delete(TEST_MODE_OUTPUTS_KEY)
            except Exception as e:
                return jsonify({"error": f"Failed to leave test mode: {e}"}), 500
            return jsonify({"success": True, **_test_mode_payload()})

        # Switching between the two stopped states keeps the original instant:
        # the tent has been uncontrolled since then either way, and restarting
        # the count would make a stop somebody forgot about look fresh.
        _, current_since = _control_mode()
        since = current_since if current_since is not None \
            else int(datetime.now(ARGENTINA_TZ).timestamp())

        if not set_setting(TEST_MODE_SETTING,
                           {"active": True, "since": since, "mode": mode}):
            return jsonify({"error": "No se pudo registrar el estado"}), 500
        try:
            redis_client.set(TEST_MODE_KEY, json.dumps({"mode": mode, "since": since}))
            if mode == MODE_PAUSED:
                # Everything starts off. Pausing is not a reason for a relay to
                # close.
                redis_client.set(
                    TEST_MODE_OUTPUTS_KEY,
                    json.dumps({name: False for name in output_names()}),
                )
            else:
                # Monitoring switches nothing, so a request left over from an
                # earlier pause must not survive into it.
                redis_client.delete(TEST_MODE_OUTPUTS_KEY)
        except Exception as e:
            return jsonify({"error": f"Failed to enter test mode: {e}"}), 500

        return jsonify({"success": True, **_test_mode_payload()})

    @app.route("/api/test-mode/outputs", methods=["POST"])
    def set_test_mode_output():
        """
        Ask an output to switch while the loop is paused.

        Refused when it is not: the control loop would ignore it, and a request
        that silently does nothing is worse than one that fails.

        The request is not a guarantee. The loop still honours the minimum
        on/off times — they protect the equipment, which does not care that a
        person asked — so an output may stay put for a few seconds. The loop
        reports what it held back.
        """
        data = request.get_json(silent=True) or {}
        name = data.get("name")
        state = data.get("state")

        if not name or not isinstance(name, str):
            return jsonify({"error": "Missing or invalid 'name'"}), 400
        if not isinstance(state, bool):
            return jsonify({"error": "Missing or invalid 'state' (must be boolean)"}), 400
        if not find_output(name):
            valid = ", ".join(output_names())
            return jsonify({"error": f"Unknown output name '{name}'. Valid names: {valid}"}), 404

        # Only the pause. Monitoring switching a relay by hand would be the one
        # thing that makes its history worthless: the whole value of those rows
        # is that they record a room with nothing running in it.
        if _control_mode()[0] != MODE_PAUSED:
            return jsonify({
                "error": "Los relés sólo se mueven a mano con el sistema en pausa. "
                         "En monitoreo nada se enciende, a propósito."
            }), 409

        try:
            raw = redis_client.get(TEST_MODE_OUTPUTS_KEY)
            requested = json.loads(raw) if raw else {}
            if not isinstance(requested, dict):
                requested = {}
            requested[name] = state
            # No TTL: these belong to the pause, and the pause is ended by
            # someone rather than by a clock. Leaving the pause deletes them.
            redis_client.set(TEST_MODE_OUTPUTS_KEY, json.dumps(requested))
        except Exception as e:
            return jsonify({"error": f"Failed to publish the request: {e}"}), 500

        return jsonify({"success": True, **_test_mode_payload()})

    # ===============================
    # Quiet hours
    # ===============================

    QUIET_HOURS_SETTING = "quiet_hours"

    @app.route("/api/quiet-hours", methods=["GET"])
    def get_quiet_hours():
        """
        The per-output quiet windows, and which outputs a window can affect.

        `automatic_outputs` is served rather than assumed: a window on an output
        nothing drives automatically would do nothing, and the dashboard should
        say so instead of offering a setting that is quietly inert.
        """
        try:
            stored = get_setting(QUIET_HOURS_SETTING, {}) or {}
            windows = windows_from_dict(stored)
            return jsonify(
                {
                    "windows": {name: w.to_dict() for name, w in windows.items()},
                    "automatic_outputs": sorted(ACTION_OUTPUTS.values()),
                    "outputs": [
                        {"name": o["name"], "label": o["label"]} for o in get_outputs()
                    ],
                }
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/quiet-hours", methods=["PUT"])
    def update_quiet_hours():
        """
        Replace the quiet windows.

        Body: {"windows": {"<output>": {"enabled": bool, "start": "HH:MM",
        "end": "HH:MM", "max_humidity": number|null}}}

        `max_humidity` is the way out: at or above it the window is ignored.
        Leaving it null is a hard block, which is a real choice — a night at
        85 % is mould, and silence is not worth a crop unless you say so.
        """
        data = request.get_json(silent=True) or {}
        raw = data.get("windows")
        if not isinstance(raw, dict):
            return jsonify({"error": "Missing or invalid 'windows' (must be an object)"}), 400

        cleaned = {}
        for name, value in raw.items():
            if not find_output(name):
                valid = ", ".join(output_names())
                return jsonify(
                    {"error": f"Unknown output name '{name}'. Valid names: {valid}"}), 400
            if not isinstance(value, dict):
                return jsonify({"error": f"'{name}' must be an object"}), 400

            enabled = bool(value.get("enabled"))
            start, end = value.get("start"), value.get("end")
            if enabled:
                # Only checked when the window is on. Half-filled configuration
                # that nobody has switched on yet is a draft, not an error.
                if parse_hhmm(start) is None or parse_hhmm(end) is None:
                    return jsonify(
                        {"error": f"'{name}': 'start' y 'end' tienen que ser HH:MM"}), 400
                if parse_hhmm(start) == parse_hhmm(end):
                    return jsonify(
                        {"error": f"'{name}': una franja de ancho cero no silencia nada"}), 400

            max_humidity = value.get("max_humidity")
            if max_humidity is not None:
                try:
                    max_humidity = float(max_humidity)
                except (TypeError, ValueError):
                    return jsonify({"error": f"'{name}': 'max_humidity' debe ser un número"}), 400
                if not 0 <= max_humidity <= 100:
                    return jsonify({"error": f"'{name}': 'max_humidity' fuera de rango (0-100)"}), 400

            cleaned[name] = QuietWindow(
                enabled=enabled,
                start=parse_hhmm(start),
                end=parse_hhmm(end),
                max_humidity=max_humidity,
            ).to_dict()

        if not set_setting(QUIET_HOURS_SETTING, cleaned):
            return jsonify({"error": "Failed to store the quiet hours"}), 500

        return jsonify({
            "success": True,
            "windows": cleaned,
            "note": "El lazo la toma en menos de 30 s.",
        })

    @app.route("/api/sensor-history", methods=["GET"])
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
                grow_id = _requested_grow()
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
            scope = _grow_scope(grow_id)

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

        Query params: hours (float) or days (int), grow_id (optional: an id,
        `all`, or absent for the active grow).
        """
        try:
            hours = request.args.get("hours", type=float)
            days = request.args.get("days", type=int)
            try:
                grow_id = _requested_grow()
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
            return jsonify({**summary, **_grow_scope(grow_id)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/history/aggregated", methods=["GET"])
    def get_history_aggregated():
        try:
            days = request.args.get("days", default=7, type=int)
            interval = request.args.get("interval", default="hourly", type=str)
            try:
                grow_id = _requested_grow()
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
                    **_grow_scope(grow_id),
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

            if stage not in STAGES:
                return jsonify({"error": f'Invalid stage. Use one of: {", ".join(STAGES)}'}), 400

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
            if stage not in STAGES:
                return jsonify({"error": f'Invalid stage. Use one of: {", ".join(STAGES)}'}), 400

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

        Query params: days (int, default 7), grow_id (optional). `all` is
        refused here on purpose: the score measures time inside *a stage's*
        band, and one number over several grows scores readings against bands
        they were never held to.
        """
        try:
            days = request.args.get("days", default=7, type=int)
            try:
                grow_id = _requested_grow()
            except ValueError as e:
                return jsonify({"error": str(e)}), 400

            score = get_vpd_score(days=days, grow_id=grow_id)
            if score.get("error"):
                return jsonify(score)
            return jsonify({**score, **_grow_scope(grow_id)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/weekly-report", methods=["GET"])
    def weekly_report_endpoint():
        """
        Get comprehensive weekly report.

        Query params: grow_id (optional). Scoped to one grow, including the
        previous-week comparison: a trend measured against a week the tent
        stood empty is not a trend.
        """
        try:
            try:
                grow_id = _requested_grow()
            except ValueError as e:
                return jsonify({"error": str(e)}), 400

            report = get_weekly_report(grow_id=grow_id)
            if report.get("error"):
                return jsonify(report)
            return jsonify({**report, **_grow_scope(grow_id)})
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
            try:
                grow_id = _requested_grow()
            except ValueError as e:
                return jsonify({"error": str(e)}), 400

            anomalies = detect_anomalies(hours=hours, grow_id=grow_id)
            # El detector mira la base, que es el lugar correcto para buscar un
            # hueco en el histórico y el equivocado para buscar un sensor
            # muerto: los dos son la misma cantidad de filas. Con el sistema en
            # pausa no hay filas a propósito, y eso salía como un "sensor
            # posiblemente desconectado" en rojo arriba de dos tarjetas verdes.
            return jsonify(reframe_history_anomalies(
                anomalies, health_snapshot(redis_client)))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ===============================
    # Feeding Endpoints
    # ===============================

    @app.route("/api/feed-plan", methods=["GET"])
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

    @app.route("/api/feed-plan", methods=["PUT"])
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

    @app.route("/api/feeding/today", methods=["GET"])
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

    @app.route("/api/irrigations", methods=["GET"])
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

    @app.route("/api/irrigations", methods=["POST"])
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

    @app.route("/api/irrigations/<int:irrigation_id>", methods=["DELETE"])
    def remove_irrigation(irrigation_id: int):
        try:
            if delete_irrigation(irrigation_id):
                return jsonify({"success": True})
            return jsonify({"error": "No existe ese riego"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/logbook", methods=["GET"])
    def get_logbook_entries():
        """
        What happened in the tent, newest first: relays, waterings and stages.

        The sentences are built here so the dashboard shows the same words
        everywhere and does not need to know the event types. The database layer
        returns raw rows; `_logbook_entry` turns each into a line.
        """
        try:
            days = min(max(request.args.get("days", default=LOGBOOK_DAYS_DEFAULT, type=int), 1),
                       LOGBOOK_DAYS_MAX)
            limit = min(max(request.args.get("limit", default=LOGBOOK_LIMIT_DEFAULT, type=int), 1),
                        LOGBOOK_LIMIT_MAX)
            since_ts = int(datetime.now(ARGENTINA_TZ).timestamp()) - days * 86400

            grow = get_active_grow()
            grow_id = int(grow["id"]) if grow else None
            grow_name = grow["name"] if grow else ""

            entries = get_logbook(since_ts, limit, grow_id=grow_id)
            return jsonify({
                "items": [_logbook_entry(entry, grow_name) for entry in entries],
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ¿Anda la instalación?, y el log de los nodos. En su propio módulo
    # porque es lo único acá que no contesta sobre la carpa sino sobre el
    # equipamiento que la mide.
    register_diagnostics(app, redis_client)

    # El PIN va último, después de que existan todas las rutas: el gate es un
    # before_request sobre la app entera, así que no puede quedar ninguna ruta
    # registrada después que se le escape. Sin AUTOCANN_PIN no instala nada y
    # todo se comporta exactamente como antes.
    register_auth(app)

    return app


# Convenience for WSGI servers
app = create_app()

