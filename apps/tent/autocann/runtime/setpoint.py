"""
Setpoint resolution: a stage becomes a humidity target and a deadband. Pure, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from autocann.control.vpd_math import (
    LEAF_TEMP_OFFSET_C,
    calculate_leaf_vpd,
    humidity_range_bounds_for_stage,
    humidity_range_for_stage,
    vpd_is_in_range,
)

#: Floor for the control deadband, so a narrow stage band still rejects sensor noise.
MIN_DEADBAND_PCT = 1.5


@dataclass(frozen=True)
class Setpoint:
    """What the controller should aim for, derived from the stage's own band."""

    target_humidity: float
    deadband_pct: float
    humidity_band: Tuple[float, float]
    leaf_temperature: float
    leaf_vpd: float
    vpd_in_range: bool


def resolve_setpoint(stage: str, air_temperature_c: float, humidity: float) -> Setpoint:
    """
    Translate a stage into a humidity setpoint and deadband.

    Every stage is expressed as a humidity band of the air: VPD stages convert
    their kPa band into the humidity that puts the *leaf* inside it, `dry` has
    an explicit band. The setpoint is the band's midpoint and the deadband is
    its half-width, so "inside the deadband" and "inside the stage's range" are
    the same statement and the controller never works to tighten something that
    is already in spec.

    The leaf sits LEAF_TEMP_OFFSET_C below the air. Leaf VPD is the leaf's
    saturation pressure minus the vapour actually in the air (see
    calculate_leaf_vpd), so both the setpoint and the in-range check use the
    leaf temperature for the VPD and the air temperature for the humidity.
    """
    leaf_temperature = round(air_temperature_c - LEAF_TEMP_OFFSET_C, 1)
    leaf_vpd = calculate_leaf_vpd(air_temperature_c, leaf_temperature, humidity)

    explicit = humidity_range_for_stage(stage)
    if explicit is not None:
        low, high = explicit
        in_range = low <= humidity <= high
    else:
        low, high = sorted(
            humidity_range_bounds_for_stage(stage, air_temperature_c, leaf_temperature)
        )
        in_range = vpd_is_in_range(leaf_vpd, stage)

    return Setpoint(
        target_humidity=round((low + high) / 2, 1),
        deadband_pct=max((high - low) / 2, MIN_DEADBAND_PCT),
        humidity_band=(low, high),
        leaf_temperature=leaf_temperature,
        leaf_vpd=leaf_vpd,
        vpd_in_range=in_range,
    )
