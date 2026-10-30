"""
Data-quality anomalies in the stored history, decided from the rows alone.

The detector behind `/api/anomalies`. It reads the 5-minute rows the loop
writes and says what is wrong with them: a hole, or a jump no tent makes. It
does not say whether a sensor is alive right now — that is `/api/health`'s job,
and `web/alert_owners.py` drops what health already says so the same fact is
not told twice.

What it no longer says, on purpose: an impossible value (nobody can act on it,
so it is discarded at the source instead — see `plausible.py`) and a value that
stopped moving (if it is there all the time it is not an alert).

Two rules shape the output:

- **One item per kind.** Every type is reported once, with how many times,
  between when, and the worst case — not one item per row.
- **What happened, then what to do.** Every message ends with the next step.

Pure: the rows and the current time come in as parameters, so the thresholds
are tested without a database. The thresholds live here, and the table in
USAGE.md ("Alertas") justifies each one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence

from autocann.control.plausible import plausible_humidity, plausible_temperature
from autocann.control.timing import SAMPLE_SAVE_INTERVAL_SECONDS
from autocann.time import ARGENTINA_TZ

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

#: The loop writes one aggregated row every save interval (5 minutes).
SAMPLE_INTERVAL_SECONDS = SAMPLE_SAVE_INTERVAL_SECONDS

#: A grow younger than this may still be waiting for its first row, which is
#: normal: no "no data" for it. Two save intervals, so one late write is covered.
FIRST_SAMPLE_GRACE_SECONDS = 2 * SAMPLE_SAVE_INTERVAL_SECONDS

#: Three missed writes. One late write is jitter; three is the recorder not
#: recording. Also the age at which /api/health calls the history stalled.
MAX_GAP_SECONDS = 3 * SAMPLE_INTERVAL_SECONDS

#: Two rows at most this far apart are compared for a jump: two writes, so a
#: single late write still counts and a gap does not.
SPIKE_WINDOW_SECONDS = 2 * SAMPLE_INTERVAL_SECONDS

#: More than this between two 5-minute averages is not the room. Lights turning
#: on move a tent 3-6 °C over half an hour; a door open, maybe 15 % RH.
TEMPERATURE_SPIKE_C = 10.0
HUMIDITY_SPIKE_PERCENT = 30.0


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------

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
        # The same two, as epochs: what `store/alerts.py` tells one occurrence
        # from the next by. The strings above are for reading.
        "first_at": first,
        "last_at": last,
    }
    item.update(extra)
    return item


def _valid(field: str, value) -> bool:
    if field == "temperature":
        return plausible_temperature(value)
    return plausible_humidity(value)


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
        # A jump to or from an impossible value (a row stored before those
        # were discarded at the source) is a broken read, not the room.
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


# ---------------------------------------------------------------------------
# The detector
# ---------------------------------------------------------------------------


def status_of(anomalies: Sequence[Dict], warnings: Sequence[Dict]) -> str:
    """
    The verdict for a report. An `info` item says something without judging the
    tent, so on its own it leaves the status at "ok".
    """
    if anomalies:
        return "critical"
    if any(item.get("severity", "warning") != "info" for item in warnings):
        return "warning"
    return "ok"


def find_anomalies(rows: Iterable[Dict], now: int, hours: int = 24,
                   tz=ARGENTINA_TZ, grow_started_at: Optional[int] = None) -> Dict:
    """
    Every anomaly in `rows` (sorted by timestamp, oldest first) as of `now`.

    Returns `anomalies` (critical), `warnings` and `status`, one item per type.
    Each row needs `timestamp`, `temperature` and `humidity`.

    `grow_started_at` (epoch) is what lets a brand-new grow with no rows yet
    say so, instead of reporting "no data" for a first sample that is simply
    not due. Without it, no rows is always `no_data`.
    """
    rows = list(rows)
    if not rows:
        if grow_started_at is not None and now - grow_started_at < FIRST_SAMPLE_GRACE_SECONDS:
            minutes = SAMPLE_SAVE_INTERVAL_SECONDS // 60
            waiting = {"type": "awaiting_first_sample", "severity": "info",
                       "message": ("Todavía no se guardó ninguna muestra: la primera se guarda "
                                   f"a los {minutes} minutos de arrancar.")}
            return {"status": "ok", "anomalies": [], "warnings": [waiting]}
        issue = {"type": "no_data", "severity": "critical", "hours": hours,
                 "timestamp": _stamp(now, tz)}
        issue["message"] = history_message(issue)
        return {"status": "critical", "anomalies": [issue], "warnings": []}

    items: List[Dict] = []
    since_last = now - rows[-1]["timestamp"]
    if since_last > MAX_GAP_SECONDS:
        issue = {"type": "stale_data", "severity": "critical",
                 "minutes_ago": since_last // 60,
                 "timestamp": _stamp(rows[-1]["timestamp"], tz),
                 # The last row: constant while nothing is stored, so the alert
                 # is the same alert for as long as the outage lasts.
                 "first_at": rows[-1]["timestamp"],
                 "last_at": rows[-1]["timestamp"]}
        issue["message"] = history_message(issue)
        items.append(issue)

    items += _gaps(rows, tz)
    for field in ("temperature", "humidity"):
        items += _spikes(rows, field, tz)

    anomalies = [i for i in items if i["severity"] == "critical"]
    warnings = [i for i in items if i["severity"] != "critical"]
    return {"status": status_of(anomalies, warnings), "anomalies": anomalies, "warnings": warnings}
