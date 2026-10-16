"""
Data-quality anomalies in the stored history, decided from the rows alone.

The detector behind `/api/anomalies`. It reads the 5-minute rows the loop
writes and says what is wrong with them: a hole, an impossible value, a jump no
tent makes, a number that stopped moving. It does not say whether a sensor is
alive right now — that is `/api/health`'s job, and `web/alert_owners.py` drops
what health already says so the same fact is not told twice.

Two rules shape the output:

- **One item per kind.** A sensor that sends garbage for forty minutes used to
  produce one item per row, and the box filled with the same sentence. Every
  type is reported once, with how many times, between when, and the worst case.
- **What happened, then what to do.** Every message ends with the next step.

Pure: the rows and the current time come in as parameters, so the thresholds
are tested without a database. The thresholds live here, in one place, and the
table in ARCHITECTURE.md ("Alertas") justifies each one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence

from autocann.time import ARGENTINA_TZ

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

#: The loop writes one aggregated row every 5 minutes.
SAMPLE_INTERVAL_SECONDS = 5 * 60

#: Three missed writes. One late write is jitter; three is the recorder not
#: recording. Also the age at which /api/health calls the history stalled.
MAX_GAP_SECONDS = 3 * SAMPLE_INTERVAL_SECONDS

#: A tent is never below -10 °C or above 60 °C with plants alive in it; past
#: that the DHT22 is answering with a failed read (it rails at -40 / 80).
TEMPERATURE_MIN_C = -10.0
TEMPERATURE_MAX_C = 60.0

#: Relative humidity is a percentage: anything outside 0-100 is a broken read.
HUMIDITY_MIN_PERCENT = 0.0
HUMIDITY_MAX_PERCENT = 100.0

#: Two rows at most this far apart are compared for a jump: two writes, so a
#: single late write still counts and a gap does not.
SPIKE_WINDOW_SECONDS = 2 * SAMPLE_INTERVAL_SECONDS

#: More than this between two 5-minute averages is not the room. Lights turning
#: on move a tent 3-6 °C over half an hour; a door open, maybe 15 % RH.
TEMPERATURE_SPIKE_C = 10.0
HUMIDITY_SPIKE_PERCENT = 30.0

#: Six identical averages in a row is 30 minutes of a number that did not move
#: by 0.01. Real air does not do that; a hung sensor serving its last value does.
STUCK_SAMPLES = 6

#: An impossible value this recent is still happening, and is critical; an older
#: one is a mark in the history and a warning. Two missed-gap windows.
ONGOING_SECONDS = 2 * MAX_GAP_SECONDS


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------

FIX_WIRING = "Revisá el cable y la alimentación del sensor."
SEE_SYSTEM = "Mirá Sistema: ahí dice si se cortó el sensor o el lazo."
CHECK_SERVICE = "Revisá el servicio en la Raspberry (make status)."

LABELS = {"temperature": "Temperatura", "humidity": "Humedad"}
UNITS = {"temperature": "°C", "humidity": "%"}


def _clock(ts: int, tz) -> datetime:
    return datetime.fromtimestamp(ts, tz)


def _stamp(ts: int, tz) -> str:
    return _clock(ts, tz).strftime("%Y-%m-%d %H:%M:%S")


def when(first: int, last: int, tz=ARGENTINA_TZ) -> str:
    """'a las 14:02' or 'entre 14:02 y 14:40', with the date when they differ."""
    a, b = _clock(first, tz), _clock(last, tz)
    fmt = "%H:%M" if a.date() == b.date() else "%d/%m %H:%M"
    if first == last or a.strftime(fmt) == b.strftime(fmt):
        return f"a las {a.strftime(fmt)}"
    return f"entre {a.strftime(fmt)} y {b.strftime(fmt)}"


def _num(value: float) -> str:
    return f"{value:.1f}"


def _times(n: int, one: str, many: str) -> str:
    return f"1 {one}" if n == 1 else f"{n} {many}"


def history_message(issue: Dict, recorder: Optional[str] = None) -> str:
    """
    The words for "nothing is being stored", with or without the culprit.

    `recorder` is who is not storing when the sensors are known to be posting
    ("el lazo de control", "el monitoreo"); without it the reader is sent to
    Sistema, which knows which of the two stopped.
    """
    if issue.get("type") == "no_data":
        what = f"No hay datos guardados en las últimas {issue.get('hours', 24)} h"
    else:
        what = f"Último dato guardado hace {issue.get('minutes_ago', '?')} min"
    if recorder:
        return (f"{what}, pero los sensores están posteando: el que no está "
                f"guardando es {recorder}. {CHECK_SERVICE}")
    return f"{what}. {SEE_SYSTEM}"


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def _group(kind: str, events: Sequence[Dict], severity: str, message: str, tz,
           **extra) -> Dict:
    """One item for `events` (each with a `ts`), compatible with one-per-row items."""
    first, last = events[0]["ts"], events[-1]["ts"]
    item = {
        "type": kind,
        "severity": severity,
        "message": message,
        # The most recent occurrence: the box prints this time next to the item.
        "timestamp": _stamp(last, tz),
        "count": len(events),
        "first": _stamp(first, tz),
        "last": _stamp(last, tz),
    }
    item.update(extra)
    return item


def _valid(field: str, value) -> bool:
    if value is None:
        return True
    if field == "temperature":
        return TEMPERATURE_MIN_C <= value <= TEMPERATURE_MAX_C
    return HUMIDITY_MIN_PERCENT <= value <= HUMIDITY_MAX_PERCENT


def _gaps(rows: Sequence[Dict], tz) -> List[Dict]:
    events = []
    for prev, row in zip(rows, rows[1:]):
        gap = row["timestamp"] - prev["timestamp"]
        if gap > MAX_GAP_SECONDS:
            events.append({"ts": prev["timestamp"], "minutes": gap // 60})
    if not events:
        return []
    longest = max(e["minutes"] for e in events)
    span = when(events[0]["ts"], events[-1]["ts"], tz)
    if len(events) == 1:
        what = f"Hubo un hueco de {longest} min sin datos {span}"
    else:
        what = f"{len(events)} huecos sin datos {span} (el más largo, {longest} min)"
    message = (f"{what}. Si no pausaste el sistema, mirá el log del nodo en Sistema "
               "para ver si se cayó el WiFi o el sensor.")
    return [_group("data_gap", events, "warning", message, tz, gap_minutes=longest)]


def _invalid(rows: Sequence[Dict], field: str, now: int, tz) -> List[Dict]:
    events = [{"ts": r["timestamp"], "value": r[field]} for r in rows
              if not _valid(field, r[field])]
    if not events:
        return []
    values = [e["value"] for e in events]
    low, high = min(values), max(values)
    unit = UNITS[field]
    shown = f"{_num(low)} {unit}" if low == high else f"de {_num(low)} a {_num(high)} {unit}"
    span = when(events[0]["ts"], events[-1]["ts"], tz)
    message = (f"{LABELS[field]} inválida: {_times(len(events), 'lectura', 'lecturas')} "
               f"{span} ({shown}). {FIX_WIRING}")
    ongoing = now - events[-1]["ts"] <= ONGOING_SECONDS
    return [_group(f"invalid_{field}", events, "critical" if ongoing else "warning",
                   message, tz, value=events[-1]["value"], min_value=low, max_value=high)]


SPIKE_LIMITS = {"temperature": TEMPERATURE_SPIKE_C, "humidity": HUMIDITY_SPIKE_PERCENT}
SPIKE_ADVICE = {
    "temperature": "Si nadie abrió la carpa ni tocó las luces, fijate que el "
                   "sensor no esté suelto o pegado a la lámpara.",
    "humidity": "Si nadie regó ni abrió la carpa, fijate que el sensor no esté "
                "mojado o suelto.",
}


def _spikes(rows: Sequence[Dict], field: str, tz) -> List[Dict]:
    events: List[Dict] = []
    for i, (prev, row) in enumerate(zip(rows, rows[1:])):
        a, b = prev[field], row[field]
        # A jump to or from an impossible value is that value's item, not a
        # second one about the same broken read.
        if a is None or b is None or not (_valid(field, a) and _valid(field, b)):
            continue
        dt = row["timestamp"] - prev["timestamp"]
        if dt > SPIKE_WINDOW_SECONDS or abs(b - a) <= SPIKE_LIMITS[field]:
            continue
        event = {"ts": row["timestamp"], "change": abs(b - a), "row": i,
                 "minutes": max(1, dt // 60), "from": a, "to": b}
        last = events[-1] if events else None
        if last and last["row"] == i - 1 and (b - a) * (last["to"] - last["from"]) < 0:
            # Up and straight back down is one excursion, not two jumps.
            if event["change"] > last["change"]:
                event["ts"] = last["ts"]
                events[-1] = event
            continue
        events.append(event)
    if not events:
        return []
    worst = max(events, key=lambda e: e["change"])
    span = when(events[0]["ts"], events[-1]["ts"], tz)
    message = (f"Cambio brusco de {LABELS[field].lower()}: "
               f"{_times(len(events), 'vez', 'veces')} {span} (hasta "
               f"{_num(worst['change'])} {UNITS[field]} en {worst['minutes']} min). "
               f"{SPIKE_ADVICE[field]}")
    return [_group(f"{field}_spike", events, "warning", message, tz,
                   change=worst["change"], from_value=worst["from"], to_value=worst["to"])]


def _stuck(rows: Sequence[Dict], field: str, tz) -> List[Dict]:
    """The first run of STUCK_SAMPLES identical valid values, measured to its end."""
    series = [r for r in rows if r[field] is not None and _valid(field, r[field])]
    i = 0
    while i < len(series):
        j = i
        while j + 1 < len(series) and series[j + 1][field] == series[i][field]:
            j += 1
        if j - i + 1 >= STUCK_SAMPLES:
            run = series[i:j + 1]
            minutes = max(1, (run[-1]["timestamp"] - run[0]["timestamp"]) // 60)
            value = run[0][field]
            message = (f"{LABELS[field]} clavada en {_num(value)} {UNITS[field]} durante "
                       f"{minutes} min, desde las {_clock(run[0]['timestamp'], tz):%H:%M}. "
                       "Puede ser un sensor colgado: si sigue así, desenchufalo y "
                       f"volvelo a enchufar. {FIX_WIRING}")
            events = [{"ts": r["timestamp"]} for r in run]
            # Still stuck at the last stored row: the same fact as a sensor that
            # stopped answering, which health may already be saying.
            return [_group(f"stuck_{field}", events, "warning", message, tz,
                           value=value, ongoing=run[-1] is series[-1])]
        i = j + 1
    return []


# ---------------------------------------------------------------------------
# The detector
# ---------------------------------------------------------------------------


def find_anomalies(rows: Iterable[Dict], now: int, hours: int = 24,
                   tz=ARGENTINA_TZ) -> Dict:
    """
    Every anomaly in `rows` (sorted by timestamp, oldest first) as of `now`.

    Returns `anomalies` (critical), `warnings` and `status`, one item per type.
    Each row needs `timestamp`, `temperature` and `humidity`.
    """
    rows = list(rows)
    if not rows:
        issue = {"type": "no_data", "severity": "critical", "hours": hours,
                 "timestamp": _stamp(now, tz)}
        issue["message"] = history_message(issue)
        return {"status": "critical", "anomalies": [issue], "warnings": []}

    items: List[Dict] = []
    since_last = now - rows[-1]["timestamp"]
    if since_last > MAX_GAP_SECONDS:
        issue = {"type": "stale_data", "severity": "critical",
                 "minutes_ago": since_last // 60,
                 "timestamp": _stamp(rows[-1]["timestamp"], tz)}
        issue["message"] = history_message(issue)
        items.append(issue)

    items += _gaps(rows, tz)
    for field in ("temperature", "humidity"):
        items += _invalid(rows, field, now, tz)
    for field in ("temperature", "humidity"):
        items += _spikes(rows, field, tz)
    for field in ("temperature", "humidity"):
        items += _stuck(rows, field, tz)

    anomalies = [i for i in items if i["severity"] == "critical"]
    warnings = [i for i in items if i["severity"] != "critical"]
    status = "critical" if anomalies else "warning" if warnings else "ok"
    return {"status": status, "anomalies": anomalies, "warnings": warnings}
