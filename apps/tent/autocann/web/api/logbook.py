"""
The logbook: one timeline of grow changes, irrigations and control events.
"""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, request

from autocann.db import (
    get_active_grow,
    get_logbook,
)
from autocann.store.grow_stages import active_stage_table
from autocann.time import ARGENTINA_TZ

bp = Blueprint("logbook", __name__)


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


def _logbook_entry(entry: dict, grow_name: str, stage_names: dict) -> dict:
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
        stage_name = stage_names.get(entry["stage"], entry["stage"])
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


@bp.route("/api/logbook", methods=["GET"])
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

        stage_names = {key: stage["name"] for key, stage in active_stage_table().items()}
        entries = get_logbook(since_ts, limit, grow_id=grow_id)
        return jsonify({
            "items": [_logbook_entry(entry, grow_name, stage_names) for entry in entries],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
