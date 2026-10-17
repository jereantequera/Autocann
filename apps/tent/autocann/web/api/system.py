"""
The dashboard page and the stage configuration it is drawn from.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template

from autocann.store.grow_stages import active_stage_table

bp = Blueprint("system", __name__)


@bp.route("/")
def index():
    """
    Serve the main dashboard page.
    """
    return render_template("index.html")


@bp.route("/api/config", methods=["GET"])
def get_config():
    """
    Stage configuration for the dashboard, read from the active grow's stages.

    Without an active grow it is the built-in profile's. A stage with no VPD
    band (a humidity-only stage) reports `null` bounds, so the dashboard can
    still read `.min` and `.max` of every stage.
    """
    table = active_stage_table()
    return jsonify(
        {
            "stages": list(table),
            "vpd_ranges": {
                key: {"min": stage["vpd_min"], "max": stage["vpd_max"]}
                for key, stage in table.items()
            },
            "humidity_ranges": {
                key: (
                    {"min": stage["rh_min"], "max": stage["rh_max"]}
                    if stage["rh_min"] is not None
                    else None
                )
                for key, stage in table.items()
            },
            "stage_names": {key: stage["name"] for key, stage in table.items()},
            # Grounds the stage proportions bar in Cultivo. The page used to
            # read it from this payload and get nothing, so the bar had no
            # expected lengths to draw against.
            "stage_expected_days": {key: stage["expected_days"] for key, stage in table.items()},
        }
    )
