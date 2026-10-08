from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Single source of truth for stage setpoints.
#
# Everything else (control loop, analytics, dashboard) must read the ranges
# from here instead of re-declaring them. They used to live in three separate
# places (this module, autocann/db.py and the dashboard template) and had
# already drifted apart.
# ---------------------------------------------------------------------------

#: VPD target band per stage, in kPa. `dry` is controlled by humidity, not VPD,
#: but it still needs a band so the analytics can score it.
VPD_RANGES: Dict[str, Tuple[float, float]] = {
    "early_veg": (0.6, 1.0),
    "late_veg": (0.8, 1.2),
    "flowering": (1.2, 1.5),
    "dry": (0.8, 1.2),
}

#: Stages driven by a relative-humidity band instead of VPD.
HUMIDITY_RANGES: Dict[str, Tuple[float, float]] = {
    "dry": (60.0, 65.0),
}

STAGES = tuple(VPD_RANGES.keys())

#: Typical length of each stage, in days. Used to show "day 12 of ~56" and to
#: estimate a harvest date; purely indicative, a grow is driven by the plant.
STAGE_EXPECTED_DAYS: Dict[str, int] = {
    "early_veg": 21,
    "late_veg": 28,
    "flowering": 56,
    "dry": 10,
}

# Kept for backwards compatibility with older imports.
EARLY_VEG_VPD_RANGE: Tuple[float, float] = VPD_RANGES["early_veg"]
LATE_VEG_VPD_RANGE: Tuple[float, float] = VPD_RANGES["late_veg"]
FLOWERING_VPD_RANGE: Tuple[float, float] = VPD_RANGES["flowering"]


def saturation_vapor_pressure(temperature_c: float) -> float:
    """
    Saturation vapor pressure in kPa (Tetens equation).
    """
    return 0.6108 * math.exp((17.27 * temperature_c) / (temperature_c + 237.3))


def calculate_humidity_for_vpd(temperature_c: float, target_vpd_kpa: float) -> float:
    """
    Relative humidity (%) needed to reach `target_vpd_kpa` at `temperature_c`.
    """
    svp = saturation_vapor_pressure(temperature_c)
    avp = svp - target_vpd_kpa
    humidity = (avp / svp) * 100

    return round(min(max(humidity, 0.0), 100.0), 2)


def calculate_vpd(temperature_c: float, humidity_percent: float) -> float:
    """
    Vapor Pressure Deficit (VPD) in kPa.
    """
    svp = saturation_vapor_pressure(temperature_c)
    avp = svp * (humidity_percent / 100.0)
    return round(svp - avp, 2)


def vpd_range_for_stage(stage: str) -> Tuple[float, float]:
    try:
        return VPD_RANGES[stage]
    except KeyError:
        raise ValueError(f"Unknown stage '{stage}'. Valid stages: {', '.join(STAGES)}") from None


def humidity_range_bounds_for_stage(stage: str, temperature_c: float) -> Tuple[float, float]:
    """
    Humidity band (%) that keeps VPD inside the stage's band at this temperature.

    Note the inversion: a *low* VPD needs *high* humidity, so the returned tuple
    is (humidity_at_vpd_min, humidity_at_vpd_max) and is therefore descending.
    """
    vpd_min, vpd_max = vpd_range_for_stage(stage)
    return (
        calculate_humidity_for_vpd(temperature_c, vpd_min),
        calculate_humidity_for_vpd(temperature_c, vpd_max),
    )


def calculate_target_humidity(stage: str, temperature_c: float) -> float:
    """
    Humidity setpoint (%) for a stage: the midpoint of the stage's humidity band.

    Pass the *same* temperature the in-range check will use. The control loop
    judges VPD at leaf temperature, so it must pass leaf temperature here too —
    passing air temperature pushes the real setpoint to the wet edge of the band.
    """
    low_bound, high_bound = humidity_range_bounds_for_stage(stage, temperature_c)
    return round((low_bound + high_bound) / 2, 0)


def vpd_is_in_range(vpd_kpa: float, stage: str) -> bool:
    """
    True when `vpd_kpa` sits inside the stage's band.

    Raises ValueError for an unknown stage: silently answering "in range" would
    make the control loop idle while believing everything is fine.
    """
    vpd_min, vpd_max = vpd_range_for_stage(stage)
    return vpd_min <= vpd_kpa <= vpd_max


def humidity_range_for_stage(stage: str) -> Optional[Tuple[float, float]]:
    """
    Explicit humidity band for stages not driven by VPD (currently `dry`).
    Returns None when the stage is VPD-driven.
    """
    return HUMIDITY_RANGES.get(stage)
