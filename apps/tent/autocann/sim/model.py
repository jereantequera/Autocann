"""
Tent model: air temperature and relative humidity in model time, and the
one-line status the simulator prints. Pure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Tuple

from autocann.control.vpd_math import calculate_vpd
from autocann.hardware.outputs import OUTPUT_NAMES

#: Outdoor air, which the tent drifts toward when nothing is switching it.
OUTDOOR_TEMP_C = 20.0
OUTDOOR_RH = 50.0
#: Exponential time constants, in model seconds.
BASE_TEMP_TAU_SECONDS = 7200.0
BASE_RH_TAU_SECONDS = 3600.0
#: With the extractor on, the tent exchanges air with the outside much faster.
VENTILATION_TAU_SECONDS = 900.0
#: Humidity change rates while a device is on, in %RH per model second.
HUMIDIFIER_RH_PER_SECOND = 0.02
DEHUMIDIFIER_RH_PER_SECOND = 0.02


def _relax(value: float, target: float, dt: float, tau: float) -> float:
    """Move `value` toward `target` over `dt`, exactly, for any `dt`."""
    return target + (value - target) * math.exp(-dt / tau)


def _clamp_rh(rh: float) -> float:
    return min(100.0, max(0.0, rh))


@dataclass
class TentModel:
    """
    Air temperature and relative humidity of the tent, in model time.

    The air relaxes toward the outdoor air; the humidifier and dehumidifier add
    and remove moisture at a constant rate; the extractor speeds the exchange.
    """

    temp: float
    rh: float
    seconds: float = 0.0

    def step(self, dt: float, outputs: Mapping[str, bool]) -> Tuple[float, float]:
        """Advance by `dt` model seconds with the given outputs on or off."""
        if dt <= 0:
            return self.temp, self.rh
        ventilating = bool(outputs.get("ventilation"))
        temp_tau = VENTILATION_TAU_SECONDS if ventilating else BASE_TEMP_TAU_SECONDS
        rh_tau = VENTILATION_TAU_SECONDS if ventilating else BASE_RH_TAU_SECONDS

        self.temp = _relax(self.temp, OUTDOOR_TEMP_C, dt, temp_tau)
        self.rh = _relax(self.rh, OUTDOOR_RH, dt, rh_tau)
        if outputs.get("humidity_up"):
            self.rh += HUMIDIFIER_RH_PER_SECOND * dt
        if outputs.get("humidity_down"):
            self.rh -= DEHUMIDIFIER_RH_PER_SECOND * dt
        self.rh = _clamp_rh(self.rh)
        self.seconds += dt
        return self.temp, self.rh


def status_line(elapsed: float, model: TentModel, outputs: Mapping[str, bool]) -> str:
    minutes, seconds = divmod(int(elapsed), 60)
    on = [name for name in OUTPUT_NAMES if outputs.get(name)]
    return (
        f"[{minutes:02d}:{seconds:02d}] T={model.temp:.1f}°C RH={model.rh:.1f}% "
        f"VPD={calculate_vpd(model.temp, model.rh):.2f} kPa  "
        f"on: {', '.join(on) if on else 'none'}"
    )
