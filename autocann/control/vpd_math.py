from __future__ import annotations

import math
from typing import Tuple


EARLY_VEG_VPD_RANGE: Tuple[float, float] = (0.6, 1.0)
LATE_VEG_VPD_RANGE: Tuple[float, float] = (0.8, 1.2)
FLOWERING_VPD_RANGE: Tuple[float, float] = (1.2, 1.5)


def calculate_humidity_for_vpd(temperature_c: float, target_vpd_kpa: float) -> float:
    """
    Calculate the relative humidity needed to achieve a target VPD at a given temperature.
    """
    svp = 0.6108 * math.exp((17.27 * temperature_c) / (temperature_c + 237.3))
    avp = svp - target_vpd_kpa
    humidity = (avp / svp) * 100

    if humidity < 0:
        humidity = 0
    elif humidity > 100:
        humidity = 100

    return round(humidity, 2)


def calculate_vpd(temperature_c: float, humidity_percent: float) -> float:
    """
    Calculate Vapor Pressure Deficit (VPD) in kPa.
    """
    svp = 0.6108 * math.exp((17.27 * temperature_c) / (temperature_c + 237.3))
    avp = svp * (humidity_percent / 100.0)
    vpd = svp - avp
    return round(vpd, 2)


def humidity_range_bounds_for_stage(stage: str, temperature_c: float) -> Tuple[float, float]:
    if stage == "early_veg":
        return (
            calculate_humidity_for_vpd(temperature_c, EARLY_VEG_VPD_RANGE[0]),
            calculate_humidity_for_vpd(temperature_c, EARLY_VEG_VPD_RANGE[1]),
        )
    if stage == "late_veg":
        return (
            calculate_humidity_for_vpd(temperature_c, LATE_VEG_VPD_RANGE[0]),
            calculate_humidity_for_vpd(temperature_c, LATE_VEG_VPD_RANGE[1]),
        )
    if stage == "flowering":
        return (
            calculate_humidity_for_vpd(temperature_c, FLOWERING_VPD_RANGE[0]),
            calculate_humidity_for_vpd(temperature_c, FLOWERING_VPD_RANGE[1]),
        )
    raise ValueError(f"Unknown stage '{stage}'")


def calculate_target_humidity(stage: str, temperature_c: float) -> float:
    low_bound, high_bound = humidity_range_bounds_for_stage(stage, temperature_c)
    return round((low_bound + high_bound) / 2, 0)


def vpd_is_in_range(vpd_kpa: float, stage: str) -> bool:
    if stage == "early_veg":
        return EARLY_VEG_VPD_RANGE[0] <= vpd_kpa <= EARLY_VEG_VPD_RANGE[1]
    if stage == "late_veg":
        return LATE_VEG_VPD_RANGE[0] <= vpd_kpa <= LATE_VEG_VPD_RANGE[1]
    if stage == "flowering":
        return FLOWERING_VPD_RANGE[0] <= vpd_kpa <= FLOWERING_VPD_RANGE[1]
    return True

