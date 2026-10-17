"""
Request helpers shared by the history and analytics endpoints: which grow a
request is about, and what the response says about it.
"""

from __future__ import annotations

from flask import request

from autocann.db import (
    ALL_GROWS,
    get_active_grow,
)


def requested_grow():
    """
    The `grow_id` query parameter, in the form the database layer wants.

    A string rather than `type=int` so that `grow_id=all` can be asked for at
    all: an integer has no spelling for "every grow", and leaving the parameter
    out has to keep meaning "whatever grow is active", which is what the
    dashboard is about.

    Raises ValueError on anything else, so a typo is a 400 rather than a
    silent fall back to a different scope than the caller asked for.
    """
    raw = (request.args.get("grow_id") or "").strip().lower()
    if not raw:
        return None
    if raw == ALL_GROWS:
        return ALL_GROWS
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"'grow_id' debe ser un número o '{ALL_GROWS}', no {raw!r}") from None


def grow_scope(grow_id):
    """
    How a response says which grow it is about.

    Served on every scoped endpoint. The dashboard cannot infer it: the default
    is resolved server-side, so a chart that does not ask gets the active grow
    and has no way to label it — and a chart whose meaning changes when
    somebody activates another grow, without saying so, is worse than one with
    no label at all.
    """
    if grow_id == ALL_GROWS:
        return {"grow_id": None, "grow_scope": ALL_GROWS}
    if grow_id is not None:
        return {"grow_id": int(grow_id), "grow_scope": "grow"}
    active = get_active_grow()
    return {
        "grow_id": int(active["id"]) if active else None,
        "grow_name": active["name"] if active else None,
        "grow_scope": "active" if active else "none",
    }
