"""
The diagnostics routes: `/api/health`, `/api/node/log` and `/api/nodes`.

Thin on purpose. Each route reads from `snapshot.py` or `nodes.py` and shapes the
JSON response; the status codes and messages are the contract the dashboard reads.
"""

from __future__ import annotations

import json
from datetime import datetime

from flask import jsonify, request

from autocann.config import is_valid_sensor_id
from autocann.time import ARGENTINA_TZ

from .health import _as_int
from .nodes import (
    LOG_MAX_LINES,
    NODE_INDEX_KEY,
    NODE_LOG_PREFIX,
    NODE_STATUS_PREFIX,
    append_log_lines,
    clean_log_lines,
    node_report,
)
from .snapshot import _json_get, health_snapshot, node_names


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
