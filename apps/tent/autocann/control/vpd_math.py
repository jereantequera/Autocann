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
#:
#: The order is the order of the cycle: `estimated_remaining_days` and the feed
#: chart's columns both read it as the sequence a grow goes through.
#:
#: `seedling` is wetter than everything after it on purpose. A sprout has
#: almost no roots and a cuticle that barely holds water, so it drinks through
#: its leaves; the dry air that keeps a flowering canopy healthy dries a
#: seedling out faster than it can drink. Half a step below early_veg, same
#: 0.2 kPa overlap the other adjacent stages have.
VPD_RANGES: Dict[str, Tuple[float, float]] = {
    "seedling": (0.4, 0.8),
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

#: Spanish display names of the stages, served by /api/config and used in the
#: logbook. Lives next to the bands so the database seed and the web app read
#: the same names.
STAGE_NAMES: Dict[str, str] = {
    "seedling": "Plántula",
    "early_veg": "Vegetativo Temprano",
    "late_veg": "Vegetativo Tardío",
    "flowering": "Floración",
    "dry": "Secado",
}

#: Typical length of each stage, in days. Used to show "day 12 of ~56" and to
#: estimate a harvest date; purely indicative, a grow is driven by the plant.
STAGE_EXPECTED_DAYS: Dict[str, int] = {
    "seedling": 14,
    "early_veg": 21,
    "late_veg": 28,
    "flowering": 56,
    "dry": 10,
}

#: Leaves run cooler than the air; VPD is judged at leaf temperature.
#: Lives here, next to the bands, because the control loop and the dashboard's
#: VPD chart must subtract the same offset or the chart disagrees with the loop.
LEAF_TEMP_OFFSET_C = 1.5

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


def calculate_leaf_vpd(
    air_temperature_c: float, leaf_temperature_c: float, humidity_percent: float
) -> float:
    """
    Leaf VPD in kPa: the saturation pressure at the leaf minus the vapour
    pressure actually in the air.

        VPD_leaf = SVP(T_leaf) - SVP(T_air) * RH / 100

    The relative humidity is measured on the air, so the vapour it stands for
    is SVP(T_air) * RH / 100 even though the leaf is the surface being judged.
    Using SVP(T_leaf) for that term too would pretend the air is saturated at
    the leaf's temperature and overstate the deficit by
    RH * (SVP(T_air) - SVP(T_leaf)) — about 0.16 kPa at 25 °C / 60 % with a
    1.5 °C leaf offset.
    """
    svp_leaf = saturation_vapor_pressure(leaf_temperature_c)
    avp_air = saturation_vapor_pressure(air_temperature_c) * (humidity_percent / 100.0)
    return round(svp_leaf - avp_air, 2)


def calculate_humidity_for_leaf_vpd(
    air_temperature_c: float, leaf_temperature_c: float, target_vpd_kpa: float
) -> float:
    """
    Relative humidity (%) of the air that gives `target_vpd_kpa` at the leaf.

    The inverse of `calculate_leaf_vpd`: solve
    SVP(T_leaf) - SVP(T_air) * RH / 100 = target for RH, which gives
    RH = (SVP(T_leaf) - target) / SVP(T_air) * 100. The denominator is the
    air's saturation pressure because the humidity is a property of the air.
    """
    svp_leaf = saturation_vapor_pressure(leaf_temperature_c)
    svp_air = saturation_vapor_pressure(air_temperature_c)
    humidity = (svp_leaf - target_vpd_kpa) / svp_air * 100

    return round(min(max(humidity, 0.0), 100.0), 2)


def vpd_range_for_stage(stage: str) -> Tuple[float, float]:
    try:
        return VPD_RANGES[stage]
    except KeyError:
        raise ValueError(f"Unknown stage '{stage}'. Valid stages: {', '.join(STAGES)}") from None


def humidity_range_bounds_for_stage(
    stage: str, air_temperature_c: float, leaf_temperature_c: Optional[float] = None
) -> Tuple[float, float]:
    """
    Humidity band (%) of the air that keeps leaf VPD inside the stage's band.

    `air_temperature_c` is the air the humidity is measured in; the leaf is at
    `leaf_temperature_c`. Without a leaf temperature the leaf is taken to be at
    the air's, and the result is the plain air-VPD band.

    Note the inversion: a *low* VPD needs *high* humidity, so the returned tuple
    is (humidity_at_vpd_min, humidity_at_vpd_max) and is therefore descending.
    """
    if leaf_temperature_c is None:
        leaf_temperature_c = air_temperature_c
    vpd_min, vpd_max = vpd_range_for_stage(stage)
    return (
        calculate_humidity_for_leaf_vpd(air_temperature_c, leaf_temperature_c, vpd_min),
        calculate_humidity_for_leaf_vpd(air_temperature_c, leaf_temperature_c, vpd_max),
    )


def calculate_target_humidity(
    stage: str, air_temperature_c: float, leaf_temperature_c: Optional[float] = None
) -> float:
    """
    Humidity setpoint (%) of the air for a stage: the midpoint of the stage's
    humidity band at this air and leaf temperature.

    Pass the same air and leaf temperatures the in-range check will use. The
    control loop judges VPD at the leaf, and the humidity it sets is the air's,
    so both temperatures are needed: the leaf sets the target VPD, the air sets
    how much vapour a given humidity means.
    """
    low_bound, high_bound = humidity_range_bounds_for_stage(
        stage, air_temperature_c, leaf_temperature_c
    )
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
