"""
Is the installation actually working, in one answer — and the nodes' own log.

Two problems, one module, because they are the same problem seen twice.

The dashboard used to answer "¿anda el sensor?" from three sources that could
not agree: the hero badge from the control loop's `sensor_status`, each sensor
card from the age of its own reading in Redis, and the alert strip from the age
of the last row in the database. A paused loop made all three say something
different at the same time — a red "sensor posiblemente desconectado" above two
green "OK" cards, with the sensor posting perfectly well throughout. So the
verdict is computed in one place, from every source at once, and served whole.

The second half is the node's log. The ESP32 prints exactly what is wrong on
its serial port — a failed DHT read, a POST that came back 400, a WiFi that
dropped — and nobody can see it, because reading that port means carrying a
laptop into the tent and unplugging the node from its power supply to plug it
into USB. So the node posts its own log here and the browser shows it.

Nothing in here decides anything about the tent. It only reports.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from flask import jsonify, request

from autocann.config import (
    INDOOR_SENSOR_MAX_AGE_SECONDS,
    MODE_ACTIVE,
    MODE_MONITOR,
    MODE_PAUSED,
    OBSERVER,
    PRIMARY,
    TEST_MODE_KEY,
    TEST_MODE_SETTING,
    UNCONTROLLED_MODES,
    WITNESS,
    indoor_sensors_from_env,
    is_valid_sensor_id,
)
from autocann.control.anomalies import CHECK_SERVICE, MAX_GAP_SECONDS
from autocann.db import ALL_GROWS, get_active_grow, get_sensor_data_range, get_setting
from autocann.time import ARGENTINA_TZ

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

#: The control loop republishes `sensors` every 3 s. Ten missed cycles is slow,
#: not dead; past this it is not running, and whatever is on screen is a
#: photograph of the past rather than the tent.
LOOP_MAX_AGE_SECONDS = 30

#: The node reports itself every 30 s, so three missed reports is silence.
NODE_MAX_AGE_SECONDS = 100

#: Three missed 5-minute writes with the loop running means the history is
#: stalling, worth saying *before* somebody goes looking for a week that was
#: never recorded. The anomaly detector's threshold, so the two cannot disagree.
HISTORY_MAX_AGE_SECONDS = MAX_GAP_SECONDS

#: How much of each node's log is kept, and how much of it one report may carry.
#: The node holds a few lines between reports; this is the scrollback a person
#: gets in the browser, which costs about 40 KB of Redis per node.
LOG_MAX_LINES = 300
LOG_MAX_LINES_PER_REPORT = 60
LOG_MAX_LINE_LENGTH = 200

LOG_LEVELS = ("info", "warn", "error")

#: Redis keys. The node's id is validated with the sensor-id pattern before it
#: is interpolated, for the same reason sensor ids are: these endpoints have no
#: authentication, and a ':' in the name would reach outside the namespace.
NODE_STATUS_PREFIX = "node_status"
NODE_LOG_PREFIX = "node_log"
NODE_INDEX_KEY = "node_index"

#: Levels, worst last, so a verdict can be taken as the maximum of its parts.
LEVELS = ("ok", "warn", "error")


def worst(*levels: Optional[str]) -> str:
    """The most serious of several levels; "ok" when there are none."""
    return max((level for level in levels if level in LEVELS),
               key=LEVELS.index, default="ok")


# ---------------------------------------------------------------------------
# One sensor
# ---------------------------------------------------------------------------

#: What each role means for the verdict. A primary that stops posting is an
#: outage: the loop has nothing to decide on and falls back to the failsafe. A
#: witness or an observer that stops is a loss of visibility, which is worth a
#: warning and is not an emergency.
ROLE_LABELS = {
    PRIMARY: "Primario · controla la carpa",
    WITNESS: "Testigo · vigila al primario",
    OBSERVER: "Observación · sólo informa",
}

ROLE_SEVERITY = {PRIMARY: "error", WITNESS: "warn", OBSERVER: "warn"}


def sensor_label(sensor_id: Optional[str]) -> str:
    """"a" reads as a label; a longer id reads as itself."""
    if not sensor_id:
        return "Sensor interior"
    if len(sensor_id) <= 2:
        return f"Sensor {sensor_id.upper()}"
    return sensor_id


def describe_age(seconds: Optional[float]) -> str:
    """An age in words, matching what the dashboard prints for the same number."""
    if seconds is None:
        return "sin datos"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"hace {seconds} s"
    minutes = seconds // 60
    if minutes < 60:
        return f"hace {minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"hace {hours} h {minutes % 60} min"
    return f"hace {hours // 24} d"


def sensor_verdict(entry: Dict, *, max_age: int = INDOOR_SENSOR_MAX_AGE_SECONDS) -> Dict:
    """
    One sensor's state, in the words the card shows.

    Three states rather than two. "Never posted" and "stopped posting" look
    identical on a card that only knows ok/error, and they send somebody to
    different places: the first to the id in the firmware and to the wiring, the
    second to the network and to the node's log.
    """
    role = entry.get("role") or OBSERVER
    severity = ROLE_SEVERITY.get(role, "warn")
    age = entry.get("age_seconds")
    flags = entry.get("flags") or []

    if not entry.get("has_reading"):
        return {
            "state": "missing",
            "level": severity,
            "state_label": "Nunca posteó",
            "state_detail": "No llegó ninguna lectura con este id.",
            "action": "Revisá que el nodo esté encendido y que el id del firmware "
                      "sea el mismo que el configurado en la Raspberry.",
        }

    if entry.get("is_stale"):
        return {
            "state": "stale",
            "level": severity,
            "state_label": "Dejó de postear",
            "state_detail": f"La última lectura es de {describe_age(age)}; "
                            f"se esperan lecturas cada {max_age} s como mucho.",
            "action": "Mirá el log del nodo en Sistema: ahí dice si falló la lectura "
                      "del DHT22, si se cayó el WiFi o si el backend rechazó el POST.",
        }

    if flags:
        # Posting, answering, in range, and wrong. This is the failure the
        # witness exists to catch, and the one a green badge hides best.
        return {
            "state": "suspect",
            "level": "warn",
            "state_label": "Postea, pero sospechoso",
            "state_detail": f"Última lectura {describe_age(age)}. "
                            "El detector de deriva lo marcó.",
            "action": "Comparalo contra un termómetro de referencia antes de "
                      "confiar en lo que mide.",
        }

    return {
        "state": "posting",
        "level": "ok",
        "state_label": "Posteando",
        "state_detail": f"Última lectura {describe_age(age)}.",
        "action": None,
    }


# ---------------------------------------------------------------------------
# The control loop and the history
# ---------------------------------------------------------------------------


def loop_verdict(payload: Optional[Dict], age_seconds: Optional[float],
                 paused_since: Optional[int], now: int,
                 mode: str = MODE_PAUSED) -> Dict:
    """
    Whether the loop is deciding, standing down, or not running.

    Standing down is checked first and on its own evidence. A stopped loop that
    also went quiet would otherwise be reported as down, and somebody would go
    restart a service that was stopped on purpose.

    Both ways of standing down are `warn` rather than `ok`. Monitoring keeps
    the history, which is the whole reason it exists, but the tent is still
    nobody's responsibility while it lasts - and a tent left uncontrolled by
    accident is the failure either mode can produce.
    """
    if paused_since is not None:
        monitoring = mode == MODE_MONITOR
        return {
            "state": MODE_MONITOR if monitoring else "paused",
            "level": "warn",
            "label": "Monitoreo" if monitoring else "En pausa",
            "detail": f"El lazo no está controlando la carpa desde "
                      f"{describe_age(now - paused_since)}. "
                      + ("El histórico se sigue guardando."
                         if monitoring else
                         "Tampoco se guarda histórico mientras tanto."),
            "age_seconds": age_seconds,
            "paused_since": paused_since,
            "paused_seconds": max(0, now - paused_since),
        }

    if payload is None or age_seconds is None:
        return {
            "state": "down",
            "level": "error",
            "label": "No está corriendo",
            "detail": "El lazo de control no publicó nada. Los relés no se están "
                      f"moviendo y lo que ves en pantalla no se está actualizando. {CHECK_SERVICE}",
            "age_seconds": age_seconds,
            "paused_since": None,
            "paused_seconds": None,
        }

    if age_seconds > LOOP_MAX_AGE_SECONDS:
        return {
            "state": "down",
            "level": "error",
            "label": "No está corriendo",
            "detail": f"La última decisión del lazo es de {describe_age(age_seconds)}. "
                      + CHECK_SERVICE,
            "age_seconds": age_seconds,
            "paused_since": None,
            "paused_seconds": None,
        }

    return {
        "state": "running",
        "level": "ok",
        "label": "Controlando",
        "detail": f"Última decisión {describe_age(age_seconds)}.",
        "age_seconds": age_seconds,
        "paused_since": None,
        "paused_seconds": None,
    }


def history_verdict(age_seconds: Optional[float], loop_state: str,
                    has_active_grow: bool = True) -> Dict:
    """
    Whether samples are reaching the database — and why not, when they are not.

    A gap in the history is read against what the loop is doing, because the
    same gap means different things: expected without a grow to file it under
    or while paused, an empty tent before the first sample, and a real fault
    only when the loop claims to be running.
    """
    if not has_active_grow:
        # Checked before the pause: unpausing would not start the history
        # either, so this is the reason that tells somebody what to do.
        return {
            "state": "no_grow",
            "level": "warn",
            "label": "Sin cultivo activo",
            "detail": "No hay cultivo activo, así que no se guardan muestras. "
                      "Creá un cultivo para empezar a registrar.",
            "age_seconds": age_seconds,
        }

    if loop_state == "paused":
        return {
            "state": "paused",
            "level": "ok",
            "label": "En pausa",
            "detail": "Mientras el sistema está en pausa no se guardan muestras, "
                      "a propósito. El hueco en los gráficos es esperado.",
            "age_seconds": age_seconds,
        }

    # Monitoring is not exempt: it writes history, so a gap in it is as real a
    # fault here as it is under control, and reporting it as expected would
    # hide exactly the failure the mode exists to avoid.

    if age_seconds is None:
        return {
            "state": "empty",
            "level": "warn",
            "label": "Sin muestras",
            "detail": "Todavía no hay ninguna muestra guardada para este cultivo.",
            "age_seconds": None,
        }

    if age_seconds > HISTORY_MAX_AGE_SECONDS:
        return {
            "state": "stalled",
            "level": "error",
            "label": "No se está guardando",
            "detail": f"La última muestra guardada es de {describe_age(age_seconds)} "
                      + ("y el monitoreo dice estar guardando. "
                         if loop_state == MODE_MONITOR else
                         "y el lazo dice estar corriendo. ") + CHECK_SERVICE,
            "age_seconds": age_seconds,
        }

    return {
        "state": "writing",
        "level": "ok",
        "label": "Guardando",
        "detail": f"Última muestra guardada {describe_age(age_seconds)}.",
        "age_seconds": age_seconds,
    }


def overall_verdict(sensors: List[Dict], loop: Dict, history: Dict,
                    nodes: List[Dict]) -> Dict:
    """
    One headline, chosen by what is worst — and naming it, not counting it.

    "3 problemas detectados" sends somebody hunting. The headline says which
    thing is broken, because that is what decides whether they walk to the tent.
    """
    level = worst(
        loop["level"], history["level"],
        *(sensor["level"] for sensor in sensors),
        *(node["level"] for node in nodes),
    )

    broken = [s for s in sensors if s["state"] in ("missing", "stale")]
    primary_broken = [s for s in broken if s["role"] == PRIMARY]

    if loop["state"] == "down":
        headline, detail = "El lazo de control no está corriendo", loop["detail"]
    elif primary_broken:
        sensor = primary_broken[0]
        headline = f"{sensor['label']} (el que controla) no está posteando"
        detail = f"{sensor['state_detail']} {sensor.get('action') or ''}".strip()
    elif history["state"] == "stalled":
        headline, detail = "No se está guardando el histórico", history["detail"]
    elif broken:
        headline = f"{broken[0]['label']} no está posteando"
        detail = f"{broken[0]['state_detail']} {broken[0].get('action') or ''}".strip()
    elif any(s["state"] == "suspect" for s in sensors):
        suspect = next(s for s in sensors if s["state"] == "suspect")
        headline = f"{suspect['label']} mide algo raro"
        detail = suspect["state_detail"]
    elif any(node["level"] != "ok" for node in nodes):
        node = next(node for node in nodes if node["level"] != "ok")
        headline, detail = f"El nodo «{node['node']}» no está reportando", node["detail"]
    elif history["state"] == "no_grow":
        headline, detail = "Sin cultivo activo", history["detail"]
    elif loop["state"] == "paused":
        headline, detail = "Sistema en pausa", loop["detail"]
    else:
        count = len(sensors)
        headline = ("Todo en orden" if count == 1
                    else f"Todo en orden · {count} sensores posteando")
        detail = f"{loop['detail']} {history['detail']}"

    return {"level": level, "headline": headline, "detail": detail}


# ---------------------------------------------------------------------------
# The nodes' own reports
# ---------------------------------------------------------------------------


def _as_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clean_log_lines(raw, received_at: int, uptime_ms: Optional[int]) -> List[Dict]:
    """
    A report's log lines, bounded and dated.

    The node counts in milliseconds since its own boot, which is the only clock
    it has. Each line is converted to wall-clock time here, by the one party
    that knows both: how long ago the node says the line happened, and when the
    report arrived. Without that the log is a column of numbers nobody can line
    up against anything else that happened in the tent.
    """
    if not isinstance(raw, list):
        return []

    lines = []
    for item in raw[:LOG_MAX_LINES_PER_REPORT]:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            continue

        text = item.get("text") or item.get("msg") or ""
        if not isinstance(text, str):
            continue
        text = text.strip()[:LOG_MAX_LINE_LENGTH]
        if not text:
            continue

        level = str(item.get("level") or "info").lower()
        if level not in LOG_LEVELS:
            level = "info"

        at = received_at
        line_ms = _as_int(item.get("ms"))
        if line_ms is not None and uptime_ms is not None:
            # Negative would mean a line from the future; the node's clock
            # jumping is not worth a wrong timestamp on screen.
            at = received_at - max(0, (uptime_ms - line_ms)) // 1000

        lines.append({
            "at": at,
            "datetime": datetime.fromtimestamp(at, ARGENTINA_TZ).strftime("%H:%M:%S"),
            "level": level,
            "text": text,
            "ms": line_ms,
        })

    return lines


def append_log_lines(stored, new: List[Dict], cap: int = LOG_MAX_LINES) -> List[Dict]:
    """The kept scrollback: oldest first, newest last, capped from the front."""
    existing = stored if isinstance(stored, list) else []
    return (existing + new)[-cap:]


def node_verdict(status: Optional[Dict], now: int) -> Dict:
    """
    What a node's own report says about itself.

    Separate from the sensors it carries: a node can be reporting happily while
    one of its two DHT22 fails every read, and that is precisely the fault this
    is meant to make visible, rather than averaging it away into "online".
    """
    if not status:
        return {
            "state": "unknown",
            "level": "warn",
            "label": "Sin reportes",
            "detail": "Este nodo nunca reportó su estado. El firmware viejo no lo "
                      "hace: regrabalo para ver su log acá.",
        }

    age = now - _as_int(status.get("received_at"), now)
    if age > NODE_MAX_AGE_SECONDS:
        return {
            "state": "silent",
            "level": "error",
            "label": "Sin contacto",
            "detail": f"El último reporte del nodo es de {describe_age(age)}.",
        }

    failing = [s for s in (status.get("sensors") or [])
               if _as_int(s.get("consecutive_failed"), 0) >= 3]
    if failing:
        names = ", ".join(sensor_label(s.get("id")) for s in failing)
        return {
            "state": "degraded",
            "level": "warn",
            "label": "Con lecturas fallidas",
            "detail": f"El nodo responde, pero no logra leer: {names}. "
                      "Suele ser el cableado del DHT22 o alimentación de 5 V donde "
                      "van 3V3.",
        }

    return {
        "state": "online",
        "level": "ok",
        "label": "En línea",
        "detail": f"Último reporte {describe_age(age)}.",
    }


def node_report(status: Optional[Dict], now: int, *, name: str) -> Dict:
    """A node as the dashboard shows it: its verdict plus what it told us."""
    status = status or {}
    received_at = _as_int(status.get("received_at"))
    uptime_ms = _as_int(status.get("uptime_ms"))

    report = {
        "node": name,
        "firmware": status.get("firmware"),
        "ip": status.get("ip"),
        "rssi": _as_int(status.get("rssi")),
        "free_heap": _as_int(status.get("free_heap")),
        "uptime_seconds": None if uptime_ms is None else uptime_ms // 1000,
        "boot_reason": status.get("boot_reason"),
        "wifi_reconnects": _as_int(status.get("wifi_reconnects")),
        "received_at": received_at,
        "age_seconds": None if received_at is None else max(0, now - received_at),
        "sensors": status.get("sensors") or [],
    }
    report.update(node_verdict(status if received_at is not None else None, now))
    return report


# ---------------------------------------------------------------------------
# Reading it all out of Redis
# ---------------------------------------------------------------------------


def _json_get(redis_client, key):
    try:
        raw = redis_client.get(key)
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def control_mode(redis_client) -> Tuple[str, Optional[int]]:
    """
    Which of the three states the tent is in, and since when.

    Redis first because it is the fast path, the settings table second because
    it is the truth: a Redis that was restarted must not read as "not paused"
    and quietly hand a tent somebody is working inside back to the controller.

    A bare timestamp in Redis is the format from before monitoring existed, and
    the only safe reading of it is the one it always had: a pause.
    """
    try:
        raw = redis_client.get(TEST_MODE_KEY)
    except Exception:
        raw = None

    if raw:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        since = _as_int(raw)
        if since is not None:
            return MODE_PAUSED, since
        try:
            mirrored = json.loads(raw)
        except (TypeError, ValueError):
            mirrored = None
        if isinstance(mirrored, dict):
            mode = mirrored.get("mode")
            return (mode if mode in UNCONTROLLED_MODES else MODE_PAUSED), \
                _as_int(mirrored.get("since"), 0)

    stored = get_setting(TEST_MODE_SETTING, None)
    if isinstance(stored, dict) and stored.get("active"):
        mode = stored.get("mode")
        return (mode if mode in UNCONTROLLED_MODES else MODE_PAUSED), \
            _as_int(stored.get("since"), 0)
    return MODE_ACTIVE, None


def paused_since(redis_client) -> Optional[int]:
    """When the loop stopped being in charge, whichever way it stopped."""
    return control_mode(redis_client)[1]


def _last_stored_sample_age(now: int) -> Optional[float]:
    """
    How long ago the loop last wrote a row, for any grow.

    Deliberately ALL_GROWS: the question here is whether the installation is
    still recording, which is about the Raspberry and not about a crop. Scoped
    to the active grow, this would read as "the history stopped" during the
    minutes between ending one grow and starting the next, when nothing is
    wrong with anything.
    """
    try:
        rows = get_sensor_data_range(limit=1, grow_id=ALL_GROWS, valid_only=False)
    except Exception:
        return None
    if not rows:
        return None
    timestamp = _as_int(rows[0].get("timestamp"))
    return None if timestamp is None else max(0, now - timestamp)


def _has_active_grow() -> bool:
    """
    Whether there is a grow for the loop to write under.

    A database that cannot be read answers yes: the history check has its own
    way of saying nothing is being stored, and a "creá un cultivo" over an
    existing grow would send somebody to make a duplicate.
    """
    try:
        return get_active_grow() is not None
    except Exception:
        return True


def indoor_entries(redis_client, sensors, now: int) -> List[Dict]:
    """
    Every configured indoor sensor with its latest reading and its age.

    Driven by the configuration rather than by what happens to be in Redis: a
    sensor that has never posted has no key, and that is exactly the sensor
    somebody needs to see on the screen.
    """
    drift = (_json_get(redis_client, "sensor_status") or {}).get("drift") or {}
    flags = drift.get("flags") or {}

    entries = []
    for role, sensor_id, key in sensors.entries():
        payload = _json_get(redis_client, key)
        entry = {
            "role": role,
            "role_label": ROLE_LABELS.get(role, role),
            "sensor_id": sensor_id,
            "label": sensor_label(sensor_id),
            "redis_key": key,
            "controls": role == PRIMARY,
            "temperature": None,
            "humidity": None,
            "vpd": None,
            "timestamp": None,
            "datetime": None,
            "age_seconds": None,
            # Never-posted is not fresh. Defaulting the other way paints an
            # empty card green.
            "is_stale": True,
            "has_reading": False,
            "flags": list(flags.get(role) or []),
        }
        if payload:
            age = max(0, now - _as_int(payload.get("timestamp"), 0))
            entry.update({
                "temperature": payload.get("temperature"),
                "humidity": payload.get("humidity"),
                "vpd": payload.get("vpd"),
                "timestamp": payload.get("timestamp"),
                "datetime": payload.get("datetime"),
                "age_seconds": age,
                "is_stale": age > INDOOR_SENSOR_MAX_AGE_SECONDS,
                "has_reading": True,
            })
        entry.update(sensor_verdict(entry))
        entries.append(entry)

    return entries


def indoor_delta(entries: List[Dict]) -> Optional[Dict]:
    """
    Witness minus primary, the direction the drift monitor measures in.

    Only for two fresh readings: subtracting a stale number from a live one
    yields a difference that is about the clock rather than about the sensors,
    and on a dashboard that difference reads as a fault in the pair.
    """
    by_role = {entry["role"]: entry for entry in entries}
    primary, witness = by_role.get(PRIMARY), by_role.get(WITNESS)
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


def node_names(redis_client) -> List[str]:
    index = _json_get(redis_client, NODE_INDEX_KEY)
    return [name for name in (index or []) if isinstance(name, str)]


def health_snapshot(redis_client) -> Dict:
    """
    Everything the dashboard needs to answer "¿anda?", computed once.

    One answer rather than four endpoints, because the four could disagree —
    and did. Whatever is on the hero badge, on each sensor card and on the alert
    strip now comes from this same object, so they cannot contradict each other
    on screen again.
    """
    now = int(datetime.now(ARGENTINA_TZ).timestamp())

    try:
        sensors_config = indoor_sensors_from_env()
    except ValueError as e:
        return {
            "level": "error",
            "headline": "La configuración de sensores está mal",
            "detail": str(e),
            "sensors": [], "nodes": [], "delta": None, "drift": None,
            "outdoor": {"ok": None, "error": None},
            "has_witness": False, "max_age_seconds": INDOOR_SENSOR_MAX_AGE_SECONDS,
            "loop": loop_verdict(None, None, None, now),
            "history": history_verdict(None, "down"),
            "generated_at": now,
        }

    entries = indoor_entries(redis_client, sensors_config, now)

    # The outdoor DHT22 hangs off the Raspberry itself, so the control loop is
    # the only one who knows about it. It rides along here anyway: its badge is
    # on the same screen as the rest, and one more source for the dashboard to
    # poll is one more source that can disagree with this one.
    status = _json_get(redis_client, "sensor_status") or {}
    outdoor = status.get("outdoor") or {}

    published = _json_get(redis_client, "sensors")
    loop_age = None
    if published:
        loop_age = max(0, now - _as_int(published.get("timestamp"), 0))
    # The published mode is believed as well as the stored pause: it is what the
    # loop itself says it is doing, and it stays right across a rename of the
    # key the pause is stored under.
    mode, since = control_mode(redis_client)
    if since is None and published and str(published.get("mode") or "") in ("test", "paused"):
        mode = MODE_PAUSED
        since = _as_int(published.get("test_mode_since")
                        or published.get("test_mode_until")) or now
    elif since is None and published and str(published.get("mode") or "") == MODE_MONITOR:
        mode, since = MODE_MONITOR, now
    loop = loop_verdict(published, loop_age, since, now, mode=mode)

    # Asked here rather than left to /api/anomalies: the dashboard polls this
    # on every view, and the anomalies stop being fetched while dismissed, so a
    # notice that hangs off them would outlive the grow that answers it.
    has_grow = _has_active_grow()
    history = history_verdict(_last_stored_sample_age(now), loop["state"],
                              has_active_grow=has_grow)

    nodes = [node_report(_json_get(redis_client, f"{NODE_STATUS_PREFIX}:{name}"),
                         now, name=name)
             for name in node_names(redis_client)]

    snapshot = {
        "sensors": entries,
        "delta": indoor_delta(entries),
        "drift": status.get("drift"),
        "outdoor": {
            "ok": outdoor.get("ok"),
            "error": outdoor.get("error"),
        },
        "has_witness": sensors_config.has_witness,
        "max_age_seconds": INDOOR_SENSOR_MAX_AGE_SECONDS,
        "loop": loop,
        "history": history,
        "no_active_grow": not has_grow,
        "nodes": nodes,
        "generated_at": now,
    }
    snapshot.update(overall_verdict(entries, loop, history, nodes))
    return snapshot


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def register_diagnostics(app, redis_client) -> None:
    """Wire the health and node-log endpoints onto an existing app."""

    @app.route("/api/health", methods=["GET"])
    def health():
        """
        Whether the installation is working, as one verdict with its evidence.

        Deliberately not cached: it is polled every few seconds by one browser
        on a LAN, and a cached answer to "¿anda?" is the one answer nobody wants.
        """
        try:
            return jsonify(health_snapshot(redis_client))
        except Exception as e:
            return jsonify({"error": f"No se pudo calcular el estado: {e}"}), 500

    def _node_name(raw, default=None):
        # Un número en `node` reventaba acá con un 500. El nodo que lo mandó mal
        # merece un 400 que lo diga, no una traza del servidor.
        candidate = raw if isinstance(raw, str) else None
        name = (candidate or default or "").strip().lower()
        return name if name and is_valid_sensor_id(name) else None

    @app.route("/api/node/log", methods=["POST"])
    def receive_node_log():
        """
        A node's own report: how it is doing, and what it printed.

        This is the serial console, carried over WiFi. Reading the real one
        means taking a laptop into the tent and unplugging the node from its
        power supply to plug it into USB — which reboots it, and so erases the
        state somebody was trying to look at.

        Unauthenticated, like the sensor endpoint it sits next to, so everything
        is bounded: the name is validated before it becomes part of a key, the
        lines are truncated and capped per report, and the stored log is capped.
        """
        data = request.get_json(silent=True) or {}

        name = _node_name(data.get("node"))
        if name is None:
            return jsonify({"error": f"node inválido: {data.get('node')!r}"}), 400

        now = int(datetime.now(ARGENTINA_TZ).timestamp())
        uptime_ms = _as_int(data.get("uptime_ms"))

        sensors = []
        for item in (data.get("sensors") or [])[:8]:
            if not isinstance(item, dict):
                continue
            sensor_id = str(item.get("id") or "").strip().lower()
            if not is_valid_sensor_id(sensor_id):
                continue
            sensors.append({
                "id": sensor_id,
                "pin": _as_int(item.get("pin")),
                "reads_ok": _as_int(item.get("reads_ok"), 0),
                "reads_failed": _as_int(item.get("reads_failed"), 0),
                "consecutive_failed": _as_int(item.get("consecutive_failed"), 0),
                "posts_ok": _as_int(item.get("posts_ok"), 0),
                "posts_failed": _as_int(item.get("posts_failed"), 0),
                "last_status": _as_int(item.get("last_status")),
            })

        status = {
            "node": name,
            "firmware": str(data.get("firmware") or "")[:40] or None,
            "ip": str(data.get("ip") or "")[:45] or None,
            "rssi": _as_int(data.get("rssi")),
            "free_heap": _as_int(data.get("free_heap")),
            "uptime_ms": uptime_ms,
            "boot_reason": str(data.get("boot_reason") or "")[:40] or None,
            "wifi_reconnects": _as_int(data.get("wifi_reconnects")),
            "sensors": sensors,
            "received_at": now,
        }

        lines = clean_log_lines(data.get("lines"), now, uptime_ms)

        try:
            redis_client.set(f"{NODE_STATUS_PREFIX}:{name}", json.dumps(status))
            if lines:
                key = f"{NODE_LOG_PREFIX}:{name}"
                redis_client.set(key, json.dumps(
                    append_log_lines(_json_get(redis_client, key), lines)))
            known = node_names(redis_client)
            if name not in known:
                redis_client.set(NODE_INDEX_KEY, json.dumps(sorted(known + [name])))
        except Exception as e:
            return jsonify({"error": f"No se pudo guardar el reporte: {e}"}), 500

        # The node is told how many lines landed so a firmware that buffers can
        # drop exactly those and keep the rest for the next report.
        return jsonify({"success": True, "accepted_lines": len(lines)})

    @app.route("/api/node/log", methods=["GET"])
    def get_node_log():
        """
        One node's log and its own report, newest line last.

        `?node=` picks it; with one node configured the name can be left out.
        """
        known = node_names(redis_client)
        requested = request.args.get("node")
        name = _node_name(requested, known[0] if len(known) == 1 else None)
        if name is None:
            return jsonify({
                "error": "Falta ?node=" if requested is None
                         else f"node inválido: {requested!r}",
                "nodes": known,
            }), 400

        limit = request.args.get("limit", default=LOG_MAX_LINES, type=int) or LOG_MAX_LINES
        limit = max(1, min(limit, LOG_MAX_LINES))
        lines = _json_get(redis_client, f"{NODE_LOG_PREFIX}:{name}") or []
        now = int(datetime.now(ARGENTINA_TZ).timestamp())

        return jsonify({
            "node": name,
            "nodes": known,
            "status": node_report(
                _json_get(redis_client, f"{NODE_STATUS_PREFIX}:{name}"), now, name=name),
            "lines": lines[-limit:],
            "max_lines": LOG_MAX_LINES,
        })

    @app.route("/api/nodes", methods=["GET"])
    def get_nodes():
        """Every node that has ever reported, with its current verdict."""
        now = int(datetime.now(ARGENTINA_TZ).timestamp())
        return jsonify({
            "nodes": [
                node_report(_json_get(redis_client, f"{NODE_STATUS_PREFIX}:{name}"),
                            now, name=name)
                for name in node_names(redis_client)
            ],
        })
