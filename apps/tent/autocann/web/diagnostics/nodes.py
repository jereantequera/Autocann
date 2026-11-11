"""
The nodes' own reports: their status, their log, and how they are shaped for the dashboard.

Pure apart from the clock, which is passed in as `now`. Reading and writing the
Redis keys is done by `snapshot.py` and `routes.py`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from autocann.config import (
    RELAY_MODE_REMOTE,
    RELAY_NODE_MAX_AGE_SECONDS,
)
from autocann.time import ARGENTINA_TZ

from .health import _as_int, describe_age, sensor_label

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


#: The node reports itself every 30 s, so three missed reports is silence.
NODE_MAX_AGE_SECONDS = 100


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


# ---------------------------------------------------------------------------
# The nodes' own reports
# ---------------------------------------------------------------------------


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
# The relay node, which reports nothing and is only ever heard from as a poll
# ---------------------------------------------------------------------------


#: The name the verdict goes under, so the dashboard and the health headline
#: call the same board the same thing as the relay rows do.
RELAY_NODE_KEY = "relay_node"


def relay_node_verdict(last_poll: Optional[Dict], now: int, *, mode: str,
                       name: str, max_age: int = RELAY_NODE_MAX_AGE_SECONDS) -> Optional[Dict]:
    """
    Whether anything is actually switching the relays — `None` when the
    question does not arise.

    The sensor node POSTs its own status, so `node_verdict` above can read what
    it says about itself. The relay node says nothing at all: it only polls
    `GET /api/outputs/desired` and applies the answer. That poll is the whole
    of the evidence, which is why it is now recorded, and why this verdict is
    about an age rather than about a report.

    The distinction this exists to draw: every relay state on screen is what
    the loop *asked for*, never what a contact did. While the node is polling
    that ask reaches a board two seconds later and the difference does not
    matter. While it is silent the two are unrelated, and a panel that keeps
    printing the ask is not describing the tent — it is describing itself.

    `None` in RELAY_MODE_GPIO, where the loop drives its own header: there is
    no poll to be late and nothing between the decision and the pin.
    """
    if mode != RELAY_MODE_REMOTE:
        return None

    at = _as_int((last_poll or {}).get("at"))
    if at is None:
        return {
            "node": RELAY_NODE_KEY,
            "name": name,
            "state": "never",
            "level": "error",
            "label": "Nunca pidió órdenes",
            "detail": f"«{name}» todavía no consultó qué hacer, así que los relés "
                      "no los está moviendo nadie. Lo que diga el panel de las "
                      "salidas es lo que el lazo pidió, no lo que está pasando.",
            "last_poll_at": None,
            "age_seconds": None,
            "ip": None,
        }

    age = max(0, now - at)
    ip = (last_poll or {}).get("ip") or None

    if age > max_age:
        return {
            "node": RELAY_NODE_KEY,
            "name": name,
            "state": "silent",
            "level": "error",
            "label": "Sin contacto",
            "detail": f"«{name}» dejó de pedir órdenes {describe_age(age)} y nadie "
                      "está conmutando los relés. Lo que muestran las salidas es lo "
                      "que el lazo pide, no lo que hay en la carpa: revisá que la "
                      "placa tenga alimentación y WiFi.",
            "last_poll_at": at,
            "age_seconds": age,
            "ip": ip,
        }

    return {
        "node": RELAY_NODE_KEY,
        "name": name,
        "state": "polling",
        "level": "ok",
        "label": "Conmutando",
        "detail": f"«{name}» pidió órdenes {describe_age(age)}.",
        "last_poll_at": at,
        "age_seconds": age,
        "ip": ip,
    }
