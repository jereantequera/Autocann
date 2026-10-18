"""
Simulator scenarios: the timeline each one plays and what the fake sensors
report along it. Pure apart from the optional random source, which tests leave out.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Optional

from autocann.sim.model import TentModel, _clamp_rh
from autocann.sim.nodes import Reading

#: Sensor noise, half-width, in the sensor's own unit.
SENSOR_NOISE = 0.1
#: Sensor "b" sits above the lamp in the real tent: warmer and drier.
OBSERVER_TEMP_OFFSET_C = 1.5
OBSERVER_RH_OFFSET = -3.0
#: The witness drifts away from the primary at this rate, in model time.
DRIFT_RATE_C_PER_HOUR = 2.0
DRIFT_MAX_C = 3.0

#: Wall-clock seconds into the run at which the sensors go silent (`stale`).
STALE_AFTER_SECONDS = 60.0
#: Wall-clock seconds into the run at which one reading is an absurd outlier.
SPIKE_AT_SECONDS = 60.0
SPIKE_TEMP_DELTA_C = 25.0

PRIMARY_SENSOR_ID = "a"
OBSERVER_SENSOR_ID = "b"


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    start_rh: float
    start_temp_c: float = 24.0


SCENARIOS: Dict[str, Scenario] = {
    s.name: s
    for s in (
        Scenario(
            "normal",
            "starts a little dry of the band; the loop should humidify into it",
            start_rh=54.0,
        ),
        Scenario(
            "humid",
            "starts far too humid; the loop should run the dehumidifier and extractor",
            start_rh=88.0,
        ),
        Scenario(
            "dry",
            "starts far too dry; the loop should run the humidifier",
            start_rh=38.0,
        ),
        Scenario(
            "stale",
            f"sensors go silent after {STALE_AFTER_SECONDS:.0f} s; the dashboard "
            "should show it and the loop should fail safe",
            start_rh=54.0,
        ),
        Scenario(
            "spike",
            f"one absurd reading ({SPIKE_TEMP_DELTA_C:+.0f} °C) at "
            f"{SPIKE_AT_SECONDS:.0f} s; the median filter should ignore it",
            start_rh=54.0,
        ),
        Scenario(
            "drift",
            "two sensors: witness 'b' drifts away from 'a'; the drift detector "
            "should flag it",
            start_rh=54.0,
        ),
        Scenario(
            "offline-relay",
            "the relay node never polls; the loop asks for outputs the tent never sees",
            start_rh=54.0,
        ),
    )
}


def initial_model(scenario: str) -> TentModel:
    spec = scenario_spec(scenario)
    return TentModel(temp=spec.start_temp_c, rh=spec.start_rh)


def scenario_spec(name: str) -> Scenario:
    try:
        return SCENARIOS[name]
    except KeyError:
        raise ValueError(f"unknown scenario {name!r}; choose from {', '.join(SCENARIOS)}") from None


def witness_offset_c(model_seconds: float) -> float:
    """How far the witness has drifted from the primary after `model_seconds`."""
    hours = model_seconds / 3600.0
    return min(DRIFT_MAX_C, DRIFT_RATE_C_PER_HOUR * hours)


def _jitter(value: float, rng: Optional[random.Random]) -> float:
    return value if rng is None else value + rng.uniform(-SENSOR_NOISE, SENSOR_NOISE)


def scenario_readings(
    name: str,
    t: float,
    model: TentModel,
    prev_t: Optional[float] = None,
    rng: Optional[random.Random] = None,
) -> Optional[List[Reading]]:
    """
    What the sensor nodes post at wall-clock second `t` of the run.

    Returns None when the sensors are silent (`stale`). The spike goes out with
    the first post at or after `SPIKE_AT_SECONDS`; `rng` adds noise, and without
    it readings are exact, as the tests need.
    """
    scenario_spec(name)  # refuses an unknown scenario
    if name == "stale" and t >= STALE_AFTER_SECONDS:
        return None

    temp = _jitter(model.temp, rng)
    rh = _clamp_rh(_jitter(model.rh, rng))
    if name == "spike" and t >= SPIKE_AT_SECONDS and (prev_t is None or prev_t < SPIKE_AT_SECONDS):
        temp += SPIKE_TEMP_DELTA_C

    readings = [Reading(PRIMARY_SENSOR_ID, temp, rh)]
    if name == "drift":
        witness_temp = model.temp + witness_offset_c(model.seconds)
        readings.append(Reading(
            OBSERVER_SENSOR_ID,
            _jitter(witness_temp, rng),
            _clamp_rh(_jitter(model.rh, rng)),
        ))
    else:
        readings.append(Reading(
            OBSERVER_SENSOR_ID,
            _jitter(model.temp + OBSERVER_TEMP_OFFSET_C, rng),
            _clamp_rh(_jitter(model.rh + OBSERVER_RH_OFFSET, rng)),
        ))
    return readings


def indoor_sensor_env(scenario: str) -> Dict[str, str]:
    """The indoor sensor roles the control loop should be started with."""
    if scenario == "drift":
        return {"AUTOCANN_INDOOR_PRIMARY": PRIMARY_SENSOR_ID,
                "AUTOCANN_INDOOR_WITNESS": OBSERVER_SENSOR_ID}
    return {"AUTOCANN_INDOOR_PRIMARY": PRIMARY_SENSOR_ID,
            "AUTOCANN_INDOOR_OBSERVERS": OBSERVER_SENSOR_ID}


def relay_polls(scenario: str) -> bool:
    """Whether the fake relay node polls at all in this scenario."""
    return scenario != "offline-relay"
