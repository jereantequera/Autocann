"""
Alerts with memory: the dashboard's anomalies, plus who has seen them.

`/api/anomalies` says what is wrong now and forgets it. `/api/alerts` says the
same, for the active grow, and remembers per alert whether the grower marked it
"Visto" or snoozed it (store/alerts.py), so that holds on every device.
"""

from __future__ import annotations

import time

from flask import Blueprint, jsonify, request

from autocann.store.active_grow import get_active_grow
from autocann.store.alerts import (
    MAX_SNOOZE_MINUTES,
    acknowledge_alert,
    snooze_alert,
    sync_alerts,
)
from autocann.web.api.analytics import current_anomalies
from autocann.web.errors import error_response

bp = Blueprint("alerts", __name__)


def _items(report):
    """The report's items, the severity each carries, the key it came under as fallback."""
    out = []
    for key, default in (("anomalies", "critical"), ("warnings", "warning")):
        for item in report.get(key) or []:
            if isinstance(item, dict):
                out.append({**item, "severity": item.get("severity") or default})
    return out


@bp.route("/api/alerts", methods=["GET"])
def list_alerts():
    """
    The active grow's alerts, each with `id`, `acknowledged_at`, `snoozed_until`
    and `unread`, and the `unread` count the bell shows.

    Recording happens here, on read: an alert exists for the grower when the
    dashboard shows it, and this is what the dashboard asks.
    """
    try:
        report = current_anomalies(hours=24)
        if not isinstance(report, dict) or report.get("error"):
            return jsonify(report if isinstance(report, dict) else {"error": "no report"}), 500
        grow = get_active_grow()
        scope = grow["id"] if grow else "none"
        items = sync_alerts(_items(report), scope, int(time.time()))
        return jsonify({
            "status": report.get("status"),
            "no_active_grow": bool(report.get("no_active_grow")),
            "items": items,
            "unread": sum(1 for item in items if item["unread"]),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/alerts/<int:alert_id>/ack", methods=["POST"])
def ack_alert(alert_id: int):
    row = acknowledge_alert(alert_id, int(time.time()))
    if row is None:
        return error_response(f"alert {alert_id} is not open", "alert_not_found", 404)
    return jsonify(row)


@bp.route("/api/alerts/<int:alert_id>/snooze", methods=["POST"])
def snooze_alert_endpoint(alert_id: int):
    minutes = (request.get_json(silent=True) or {}).get("minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, int) \
            or not 1 <= minutes <= MAX_SNOOZE_MINUTES:
        return error_response(
            f"minutes must be an integer between 1 and {MAX_SNOOZE_MINUTES}",
            "invalid_snooze", 400)
    row = snooze_alert(alert_id, int(time.time()) + minutes * 60)
    if row is None:
        return error_response(f"alert {alert_id} is not open", "alert_not_found", 404)
    return jsonify(row)
