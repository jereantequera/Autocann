"""
Rules for a crop profile's stages. Pure: no database, no I/O.

Mirrors `crates/protocol/src/profile.rs`, so a profile the tent accepts here is
one the devices accept too.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

#: Upper limit of a leaf-VPD band, in kPa.
VPD_MAX_KPA = 4.0
#: Upper limit of a relative-humidity band, in %.
RH_MAX_PERCENT = 100.0


def _band(stage: Dict, low_key: str, high_key: str) -> Tuple[Optional[float], Optional[float]]:
    return stage.get(low_key), stage.get(high_key)


def _check_band(label: str, low: float, high: float, upper: float, unit: str) -> List[str]:
    if low < 0 or high > upper or low >= high:
        return [
            f"{label}: the band must satisfy 0 <= min < max <= {upper:g} {unit}, "
            f"got {low:g}..{high:g}"
        ]
    return []


def validate_profile_stages(stages: List[Dict]) -> List[str]:
    """
    Human-readable errors for a profile's stages, in list order. Empty when valid.

    Each stage is a dict with `key`, `expected_days`, `vpd_min`, `vpd_max`,
    `rh_min` and `rh_max` (missing values count as None).
    """
    if not stages:
        return ["the profile needs at least one stage"]

    errors: List[str] = []
    seen_keys = set()
    last_index = len(stages) - 1

    for index, stage in enumerate(stages):
        key = stage.get("key")
        label = f"stage '{key}'" if key else f"stage #{index + 1}"

        if key in seen_keys:
            errors.append(f"{label}: the key is used by more than one stage")
        seen_keys.add(key)

        vpd_min, vpd_max = _band(stage, "vpd_min", "vpd_max")
        rh_min, rh_max = _band(stage, "rh_min", "rh_max")
        has_vpd = vpd_min is not None or vpd_max is not None
        has_rh = rh_min is not None or rh_max is not None
        vpd_complete = vpd_min is not None and vpd_max is not None
        rh_complete = rh_min is not None and rh_max is not None

        if not vpd_complete and not rh_complete and not (has_vpd or has_rh):
            errors.append(f"{label}: needs a VPD band (vpd_min, vpd_max) or an RH band (rh_min, rh_max)")

        if has_vpd and not vpd_complete:
            errors.append(f"{label}: the VPD band is incomplete, it needs both vpd_min and vpd_max")
        elif vpd_complete:
            errors += _check_band(f"{label} VPD band", vpd_min, vpd_max, VPD_MAX_KPA, "kPa")

        if has_rh and not rh_complete:
            errors.append(f"{label}: the RH band is incomplete, it needs both rh_min and rh_max")
        elif rh_complete:
            errors += _check_band(f"{label} RH band", rh_min, rh_max, RH_MAX_PERCENT, "%")

        expected_days = stage.get("expected_days")
        if expected_days is None:
            if index != last_index:
                errors.append(f"{label}: only the last stage may be open-ended (expected_days None)")
        elif expected_days <= 0:
            errors.append(f"{label}: expected_days must be positive, got {expected_days}")

    return errors
