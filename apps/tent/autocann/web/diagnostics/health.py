"""
The health verdicts: pure functions that turn readings into "ok", "warn" or "error".

Nothing here touches Redis, the database or the clock. Every verdict is computed
from its inputs, so the rules can be tested without an installation behind them.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from autocann.config import (
    INDOOR_SENSOR_MAX_AGE_SECONDS,
    MODE_MONITOR,
    MODE_PAUSED,
    OBSERVER,
    PRIMARY,
    WITNESS,
)
from autocann.control.anomalies import CHECK_SERVICE, MAX_GAP_SECONDS

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


#: The control loop republishes `sensors` every 3 s. Ten missed cycles is slow,
#: not dead; past this it is not running, and whatever is on screen is a
#: photograph of the past rather than the tent.
LOOP_MAX_AGE_SECONDS = 30


#: Three missed 5-minute writes with the loop running means the history is
#: stalling, worth saying *before* somebody goes looking for a week that was
#: never recorded. The anomaly detector's threshold, so the two cannot disagree.
HISTORY_MAX_AGE_SECONDS = MAX_GAP_SECONDS


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


def _as_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
