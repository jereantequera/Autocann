"""
Timing limits for the humidity controller, tunable per installation from the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ControlTuning:
    """
    Timing limits for the humidity controller.

    These protect the hardware, so they are tunable per installation without
    editing code. A dehumidifier with a compressor wants a longer `min_off`
    (300s is a reasonable floor); a small ultrasonic humidifier tolerates less.
    """

    min_on_seconds: float = 60.0
    min_off_seconds: float = 180.0
    min_changeover_seconds: float = 120.0
    stale_after_seconds: float = 120.0


def control_tuning_from_env() -> ControlTuning:
    defaults = ControlTuning()

    def _get_float(name: str, default: float) -> float:
        val = os.getenv(name)
        if val is None or val.strip() == "":
            return default
        try:
            parsed = float(val)
        except ValueError:
            raise ValueError(f"{name}={val!r} is not a number") from None
        if parsed < 0:
            raise ValueError(f"{name}={val!r} must not be negative")
        return parsed

    return ControlTuning(
        min_on_seconds=_get_float("AUTOCANN_MIN_ON_SECONDS", defaults.min_on_seconds),
        min_off_seconds=_get_float("AUTOCANN_MIN_OFF_SECONDS", defaults.min_off_seconds),
        min_changeover_seconds=_get_float(
            "AUTOCANN_MIN_CHANGEOVER_SECONDS", defaults.min_changeover_seconds
        ),
        stale_after_seconds=_get_float(
            "AUTOCANN_STALE_AFTER_SECONDS", defaults.stale_after_seconds
        ),
    )
