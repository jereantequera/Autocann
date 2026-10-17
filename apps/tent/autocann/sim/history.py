"""
Synthetic history for the simulator: days of samples shaped like the loop's.

Pure, like `autocann/control/`: no database, no clock beyond the `now` it is
given, and randomness only from `seed`. The rows carry the keys that
`SampleAccumulator.build()` produces, plus a `timestamp`, so the store takes
them unchanged.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from autocann.control.vpd_math import (
    VPD_RANGES,
    calculate_humidity_for_vpd,
    calculate_leaf_vpd,
    calculate_vpd,
)
from autocann.time import ARGENTINA_TZ

DAY_SECONDS = 86400
DEFAULT_INTERVAL_SECONDS = 300
#: Readings averaged into one stored row, as the loop does at 5 s per reading.
READINGS_PER_ROW = 60
#: The lights come on at this local hour.
LIGHTS_ON_HOUR = 6
#: Leaves run this much cooler than the air, as in the control loop.
LEAF_OFFSET_C = 1.5
#: Air temperature with the lights on and off, and the outdoor air at each.
DAY_TEMP_C = 25.0
NIGHT_TEMP_C = 20.5
OUTDOOR_DAY_C = 22.0
OUTDOOR_NIGHT_C = 15.0
#: Out-of-band stretches: about one a day, lasting this many rows each.
EXCURSIONS_PER_DAY = 1
EXCURSION_ROWS = (6, 12)
#: How far outside the band an excursion pushes the VPD, in kPa.
EXCURSION_KPA = 0.3
#: How far from the band midpoint an ordinary reading wanders, in kPa.
VPD_JITTER_KPA = 0.05
#: Lights on, the air is drier: the VPD sits this far above the midpoint, and
#: this far below it with the lights off. Humidity alone would not show it,
#: because warm air holds more water: the same VPD reads a lower RH by day.
DAY_VPD_SHIFT_KPA = 0.1

#: `(start_hours, hours)`: a hole that begins this many hours after the first sample.
Gap = Tuple[float, float]


def lights_on(timestamp: int, lights_on_hours: float) -> bool:
    """Whether the lights are on at this instant, by local clock."""
    local = datetime.fromtimestamp(timestamp, ARGENTINA_TZ)
    hour = local.hour + local.minute / 60.0
    return (hour - LIGHTS_ON_HOUR) % 24.0 < lights_on_hours


def stage_at(timestamp: int, start_stage: str, stage_changes: Sequence[Tuple[int, str]]) -> str:
    """The stage in force at `timestamp`, given the stage the grow started in."""
    stage = start_stage
    for changed_at, new_stage in sorted(stage_changes):
        if changed_at <= timestamp:
            stage = new_stage
    return stage


def _excursions(count: int, days: float, rng: random.Random) -> Dict[int, float]:
    """Row index -> +1 (too dry) or -1 (too humid) for each out-of-band stretch."""
    marked: Dict[int, float] = {}
    for _ in range(int(days * EXCURSIONS_PER_DAY)):
        index = rng.randrange(max(1, count))
        length = rng.randint(*EXCURSION_ROWS)
        sign = 1.0 if rng.random() < 0.7 else -1.0
        for offset in range(min(length, count - index)):
            marked[index + offset] = sign
    return marked


def _in_gap(timestamp: int, first: int, gaps: Sequence[Gap]) -> bool:
    for start_h, hours in gaps:
        begin = first + start_h * 3600
        if begin <= timestamp < begin + hours * 3600:
            return True
    return False


def _action(humidity: float, target: float) -> Optional[str]:
    if humidity < target - 3:
        return "humidity_up"
    if humidity > target + 3:
        return "humidity_down"
    return None


def synthetic_history(
    days: float,
    now: int,
    *,
    interval_s: int = DEFAULT_INTERVAL_SECONDS,
    lights_on_hours: float = 18.0,
    gaps: Optional[Sequence[Gap]] = None,
    seed: int = 0,
    start_stage: str = "early_veg",
    stage_changes: Sequence[Tuple[int, str]] = (),
) -> List[Dict[str, Any]]:
    """
    Rows for the last `days` days, ending at `now`, one per `interval_s`.

    The grid is aligned to `interval_s`, so there are `days * 86400 / interval_s`
    rows before the gaps are dropped. The air sits mostly in the middle of the
    stage's VPD band, with a few stretches outside it, and it is warmer and
    drier while the lights are on. Same `seed`, same rows.
    """
    if days <= 0 or interval_s <= 0:
        raise ValueError("days and interval_s must be positive")
    count = int(days * DAY_SECONDS) // interval_s
    last = (now // interval_s) * interval_s
    first = last - (count - 1) * interval_s
    rng = random.Random(seed)
    excursions = _excursions(count, days, rng)

    rows: List[Dict[str, Any]] = []
    for index in range(count):
        timestamp = first + index * interval_s
        if _in_gap(timestamp, first, gaps or ()):
            continue
        day = lights_on(timestamp, lights_on_hours)
        stage = stage_at(timestamp, start_stage, stage_changes)
        low, high = VPD_RANGES[stage]
        target_vpd = (low + high) / 2 + (DAY_VPD_SHIFT_KPA if day else -DAY_VPD_SHIFT_KPA)
        if index in excursions:
            target_vpd = high + EXCURSION_KPA if excursions[index] > 0 else low - EXCURSION_KPA
        target_vpd += rng.uniform(-VPD_JITTER_KPA, VPD_JITTER_KPA)

        temp = (DAY_TEMP_C if day else NIGHT_TEMP_C) + rng.uniform(-0.15, 0.15)
        temp += 0.3 * math.sin(2 * math.pi * index / 288.0)
        humidity = calculate_humidity_for_vpd(temp, target_vpd)
        leaf_temp = temp - LEAF_OFFSET_C
        outside_temp = (OUTDOOR_DAY_C if day else OUTDOOR_NIGHT_C) + rng.uniform(-0.3, 0.3)
        outside_rh = (55.0 if day else 70.0) + rng.uniform(-2.0, 2.0)
        target_rh = calculate_humidity_for_vpd(temp, (low + high) / 2)

        rows.append({
            "timestamp": timestamp,
            "temperature": round(temp, 2),
            "humidity": round(humidity, 2),
            "vpd": calculate_vpd(temp, humidity),
            "leaf_temperature": round(leaf_temp, 2),
            "leaf_vpd": calculate_leaf_vpd(temp, leaf_temp, humidity),
            "outside_temperature": round(outside_temp, 2),
            "outside_humidity": round(outside_rh, 2),
            "target_humidity": target_rh,
            "temperature_min": round(temp - 0.3, 2),
            "temperature_max": round(temp + 0.3, 2),
            "humidity_min": round(humidity - 0.5, 2),
            "humidity_max": round(humidity + 0.5, 2),
            "sample_n": READINGS_PER_ROW,
            "stage": stage,
            "indoor_source": "esp32",
            "control_action": _action(humidity, target_rh),
            "quality": "ok",
            "temperature_raw": round(temp, 2),
            "humidity_raw": round(humidity, 2),
            "outside_temperature_raw": round(outside_temp, 2),
            "outside_humidity_raw": round(outside_rh, 2),
        })
    return rows


@dataclass
class HistoryPlan:
    """Everything the seeder writes for one simulated grow."""

    rows: List[Dict[str, Any]]
    #: The stage the grow was created in, and the later changes as (timestamp, stage).
    start_stage: str = "early_veg"
    stage_changes: List[Tuple[int, str]] = field(default_factory=list)
    irrigations: List[Dict[str, Any]] = field(default_factory=list)


def default_gaps(days: float) -> List[Gap]:
    """Two two-hour holes, a third and two thirds of the way through."""
    span = days * 24
    return [(span / 3, 2.0), (2 * span / 3, 2.0)]


def plan_history(days: int, now: int, *, seed: int = 0,
                 gaps: Optional[Sequence[Gap]] = None) -> HistoryPlan:
    """
    The rows, a past stage change and the waterings for `days` of history.

    The grow starts with the first row. From day 3 on it moves from early to
    late vegetative 60 % of the way through, so the timeline has a past change
    and the stage the grow ends in is the one the loop runs with. A watering
    every other day, at 08:00 of the day it falls on, feeds the Bitácora.
    """
    rows_end = (now // DEFAULT_INTERVAL_SECONDS) * DEFAULT_INTERVAL_SECONDS
    start = rows_end - days * DAY_SECONDS + DEFAULT_INTERVAL_SECONDS
    changes: List[Tuple[int, str]] = []
    if days >= 3:
        changes.append((start + int(days * DAY_SECONDS * 0.6), "late_veg"))
    rows = synthetic_history(days, now, gaps=gaps, seed=seed, stage_changes=changes)

    rng = random.Random(seed + 1)
    irrigations: List[Dict[str, Any]] = []
    for day in range(1, days, 2):
        when = start + day * DAY_SECONDS + 8 * 3600 - 3 * 3600  # 08:00 local (UTC-3)
        if when > now:
            break
        irrigations.append({
            "timestamp": when,
            "litres": round(rng.uniform(1.5, 2.5), 1),
            "items": [
                {"name": "Nutriente base", "unit": "ml/L", "dose": round(2.0 + day / 7, 1)},
                {"name": "Calmag", "unit": "ml/L", "dose": 1.0, "on_plan": day % 4 == 1},
            ],
            "ph": round(rng.uniform(5.9, 6.3), 1),
            "ec": round(rng.uniform(1.2, 1.6), 2),
            "stage": stage_at(when, "early_veg", changes),
            "week": day // 7 + 1,
            "notes": "",
        })
    return HistoryPlan(rows=rows, stage_changes=changes, irrigations=irrigations)
