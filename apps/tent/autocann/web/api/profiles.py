"""
Crop profiles: list, read, copy, edit and delete.

Built-in profiles can be read and copied, never edited or deleted. A profile
used by an active grow cannot be deleted. Editing a profile never changes a
grow, which runs from its own snapshot (see store/grows.py).
"""

from __future__ import annotations

import math
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from autocann.control.profiles import validate_profile_stages
from autocann.db import (
    ProfileError,
    ProfileInUse,
    ProfileNotFound,
    ProfileReadOnly,
    copy_profile,
    delete_profile,
    get_profile,
    get_profiles,
    update_profile,
)

bp = Blueprint("profiles", __name__)

NAME_MAX_LENGTH = 80
KEY_PATTERN = re.compile(r"^[a-z0-9_]{1,32}$")
BAND_FIELDS = ("vpd_min", "vpd_max", "rh_min", "rh_max")


# ===============================
# Input checks
# ===============================

def _json_object() -> Optional[Dict[str, Any]]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _clean_name(value: Any) -> Optional[str]:
    """The name stripped of surrounding space, or None when it is not a usable name."""
    if not isinstance(value, str):
        return None
    name = value.strip()
    return name if 0 < len(name) <= NAME_MAX_LENGTH else None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _parse_stages(raw: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Shape-check the stages from a request body, before any rule is applied.

    Returns the stages as the store takes them, and the errors found. Only when
    there are no shape errors are the stages worth checking against the rules.
    """
    if not isinstance(raw, list) or not raw:
        return [], ["stages must be a non-empty list"]

    stages: List[Dict[str, Any]] = []
    errors: List[str] = []
    for index, item in enumerate(raw):
        label = f"stage #{index + 1}"
        if not isinstance(item, dict):
            errors.append(f"{label}: must be an object")
            continue

        key = item.get("key")
        if not isinstance(key, str) or not KEY_PATTERN.match(key):
            errors.append(f"{label}: key must be 1-32 characters of a-z, 0-9 or _")
        name = _clean_name(item.get("name"))
        if name is None:
            errors.append(f"{label}: name must be a non-empty string of at most {NAME_MAX_LENGTH} characters")

        expected_days = item.get("expected_days")
        if expected_days is not None and (isinstance(expected_days, bool) or not isinstance(expected_days, int)):
            errors.append(f"{label}: expected_days must be an integer or null")
        for field in BAND_FIELDS:
            value = item.get(field)
            if value is not None and not _is_number(value):
                errors.append(f"{label}: {field} must be a number or null")
        water_only = item.get("water_only", False)
        if not isinstance(water_only, bool):
            errors.append(f"{label}: water_only must be true or false")

        stages.append({
            "key": key,
            "name": name,
            "expected_days": expected_days,
            **{field: item.get(field) for field in BAND_FIELDS},
            "water_only": water_only,
        })
    return stages, errors


def _not_an_object():
    return jsonify({"errors": ["the body must be a JSON object"]}), 400


# ===============================
# Errors from the store
# ===============================

@bp.errorhandler(ProfileNotFound)
def _not_found(error: ProfileError):
    return jsonify({"error": str(error)}), 404


@bp.errorhandler(ProfileReadOnly)
def _read_only(error: ProfileError):
    return jsonify({"error": str(error)}), 403


@bp.errorhandler(ProfileInUse)
def _in_use(error: ProfileError):
    return jsonify({"error": str(error)}), 409


@bp.errorhandler(sqlite3.Error)
def _database_error(error: sqlite3.Error):
    return jsonify({"error": str(error)}), 500


# ===============================
# Profile endpoints
# ===============================

@bp.route("/api/profiles", methods=["GET"])
def list_profiles():
    profiles = get_profiles()
    return jsonify({"profiles": profiles, "count": len(profiles)})


@bp.route("/api/profiles/<profile_id>", methods=["GET"])
def read_profile(profile_id: str):
    profile = get_profile(profile_id)
    if profile is None:
        return jsonify({"error": f"profile '{profile_id}' does not exist"}), 404
    return jsonify(profile)


@bp.route("/api/profiles/<profile_id>/copy", methods=["POST"])
def copy_profile_endpoint(profile_id: str):
    data = _json_object()
    if data is None:
        return _not_an_object()
    name = _clean_name(data.get("name"))
    if name is None:
        return jsonify({"errors": [f"name must be a non-empty string of at most {NAME_MAX_LENGTH} characters"]}), 400

    new_id = copy_profile(profile_id, name)
    return jsonify(get_profile(new_id)), 201


@bp.route("/api/profiles/<profile_id>", methods=["PUT"])
def edit_profile(profile_id: str):
    # Checked before the body, so a built-in profile answers 403 whatever it is sent.
    existing = get_profile(profile_id)
    if existing is None:
        return jsonify({"error": f"profile '{profile_id}' does not exist"}), 404
    if existing["kind"] == "builtin":
        return jsonify({"error": f"profile '{profile_id}' is built-in and cannot be changed"}), 403

    data = _json_object()
    if data is None:
        return _not_an_object()
    name = _clean_name(data.get("name"))
    stages, errors = _parse_stages(data.get("stages"))
    if name is None:
        errors.insert(0, f"name must be a non-empty string of at most {NAME_MAX_LENGTH} characters")
    if not errors:
        errors = validate_profile_stages(stages)
    if errors:
        return jsonify({"errors": errors}), 400

    update_profile(profile_id, name, stages)
    return jsonify(get_profile(profile_id))


@bp.route("/api/profiles/<profile_id>", methods=["DELETE"])
def remove_profile(profile_id: str):
    delete_profile(profile_id)
    return "", 204
