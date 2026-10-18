"""
The tent: its name, over the store.

The rig has one tent, id 1 (store/seeds.py). The routes take the id anyway, so
the URLs do not change when there are more.
"""

from __future__ import annotations

from typing import Any, Optional

from flask import Blueprint, jsonify, request

from autocann.store.tents import get_tent, rename_tent
from autocann.web.errors import errors_response

bp = Blueprint("tents", __name__)

NAME_MAX_LENGTH = 60


def _clean_name(value: Any) -> Optional[str]:
    """The name stripped of surrounding space, or None when it is not a usable name."""
    if not isinstance(value, str):
        return None
    name = value.strip()
    return name if 0 < len(name) <= NAME_MAX_LENGTH else None


@bp.route("/api/tents/<int:tent_id>", methods=["GET"])
def read_tent(tent_id: int):
    tent = get_tent(tent_id)
    if tent is None:
        return jsonify({"error": f"tent {tent_id} does not exist"}), 404
    return jsonify(tent)


@bp.route("/api/tents/<int:tent_id>", methods=["PUT"])
def rename_tent_endpoint(tent_id: int):
    if get_tent(tent_id) is None:
        return jsonify({"error": f"tent {tent_id} does not exist"}), 404

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return errors_response(["the body must be a JSON object"], "invalid_body", 400)
    name = _clean_name(data.get("name"))
    if name is None:
        return errors_response([f"name must be a non-empty string of at most {NAME_MAX_LENGTH} characters"], "invalid_name", 400)

    rename_tent(tent_id, name)
    return jsonify(get_tent(tent_id))
