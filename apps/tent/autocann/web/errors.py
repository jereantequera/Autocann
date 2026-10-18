"""
Error bodies for the endpoints a form shows inline.

The `error` (or `errors`) text stays English: it is for logs and developers.
`code` is the machine-readable part the dashboard maps to Spanish
(static/js/errors.js). Keep the codes in sync with that map.
"""

from __future__ import annotations

from typing import List

from flask import jsonify


def error_response(message: str, code: str, status: int):
    """A `{"error": message, "code": code}` body with its status."""
    return jsonify({"error": message, "code": code}), status


def errors_response(messages: List[str], code: str, status: int):
    """The `{"errors": [...], "code": code}` body the profile and tent routes use."""
    return jsonify({"errors": messages, "code": code}), status
