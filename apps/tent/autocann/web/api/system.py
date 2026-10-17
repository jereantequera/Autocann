"""
The dashboard page and the stage configuration it is drawn from.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template

from autocann.control.vpd_math import (
    STAGE_EXPECTED_DAYS,
    STAGE_NAMES,
    STAGES,
    VPD_RANGES,
    humidity_range_for_stage,
)

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
    Stage configuration for the dashboard.

    The ranges used to be a hardcoded copy in the template, which is how the
    template and vpd_math.py drifted apart. Serving them keeps one source.
    """
    return jsonify(
        {
            "stages": list(STAGES),
            "vpd_ranges": {
                stage: {"min": low, "max": high} for stage, (low, high) in VPD_RANGES.items()
            },
            "humidity_ranges": {
                stage: ({"min": band[0], "max": band[1]} if band else None)
                for stage in STAGES
                for band in [humidity_range_for_stage(stage)]
            },
            "stage_names": STAGE_NAMES,
            # Grounds the stage proportions bar in Cultivo. The page used to
            # read it from this payload and get nothing, so the bar had no
            # expected lengths to draw against.
            "stage_expected_days": STAGE_EXPECTED_DAYS,
        }
    )
