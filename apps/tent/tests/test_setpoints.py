from __future__ import annotations

import pytest

from autocann.cli.vpd import resolve_setpoint
from autocann.control.vpd_math import (
    LEAF_TEMP_OFFSET_C,
    STAGES,
    VPD_RANGES,
    calculate_humidity_for_leaf_vpd,
    calculate_leaf_vpd,
    calculate_target_humidity,
    calculate_vpd,
    humidity_range_bounds_for_stage,
    saturation_vapor_pressure,
    vpd_is_in_range,
)

TEMPERATURES = [18.0, 20.0, 22.0, 24.0, 26.0, 28.0, 30.0]
HUMIDITIES = [30.0, 40.0, 50.0, 55.0, 60.0, 65.0, 70.0, 80.0, 90.0]


@pytest.mark.parametrize("stage", [s for s in STAGES if s != "dry"])
@pytest.mark.parametrize("temperature", TEMPERATURES)
@pytest.mark.parametrize("humidity", HUMIDITIES)
def test_deadband_and_vpd_range_always_agree(stage, temperature, humidity):
    """
    The controller must not act while VPD is already in range, and must act once
    it leaves. That only holds if "inside the deadband" and "VPD in range" are
    the same statement — the invariant the deadband is derived from.
    """
    setpoint = resolve_setpoint(stage, temperature, humidity)
    inside_deadband = abs(humidity - setpoint.target_humidity) <= setpoint.deadband_pct
    assert inside_deadband == setpoint.vpd_in_range


@pytest.mark.parametrize("stage", [s for s in STAGES if s != "dry"])
@pytest.mark.parametrize("temperature", TEMPERATURES)
def test_hitting_the_target_lands_mid_band_not_at_an_edge(stage, temperature):
    """
    The old loop derived the target from air temperature but judged VPD at leaf
    temperature, which put the real setpoint at the wet edge of the band. At the
    target, leaf VPD should sit near the middle of the stage's kPa band.
    """
    setpoint = resolve_setpoint(stage, temperature, 50.0)
    vpd_at_target = calculate_leaf_vpd(
        temperature, setpoint.leaf_temperature, setpoint.target_humidity
    )
    vpd_min, vpd_max = VPD_RANGES[stage]

    assert vpd_is_in_range(vpd_at_target, stage)
    midpoint = (vpd_min + vpd_max) / 2
    assert abs(vpd_at_target - midpoint) <= (vpd_max - vpd_min) * 0.2


@pytest.mark.parametrize("temperature", TEMPERATURES)
def test_dry_stage_uses_its_explicit_humidity_band(temperature):
    setpoint = resolve_setpoint("dry", temperature, 62.0)
    assert setpoint.humidity_band == (60.0, 65.0)
    assert setpoint.target_humidity == 62.5
    assert setpoint.deadband_pct == 2.5


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("temperature", TEMPERATURES)
def test_target_always_sits_inside_its_own_band(stage, temperature):
    setpoint = resolve_setpoint(stage, temperature, 55.0)
    low, high = setpoint.humidity_band
    assert low <= setpoint.target_humidity <= high
    assert 0.0 <= setpoint.target_humidity <= 100.0


@pytest.mark.parametrize("temperature", TEMPERATURES)
def test_a_seedling_is_asked_to_be_wetter_than_early_veg(temperature):
    """
    The point of the stage: the same tent, told to hold more water in the air.
    If the target came out at or below early_veg's, adding the stage would have
    fixed the day counter and left the loop chasing the old setpoint.
    """
    seedling = resolve_setpoint("seedling", temperature, 60.0)
    early = resolve_setpoint("early_veg", temperature, 60.0)
    assert seedling.target_humidity > early.target_humidity
    # And still something a humidifier can reach, not 100%.
    assert seedling.target_humidity <= 90.0


def test_unknown_stage_is_rejected_rather_than_silently_accepted():
    with pytest.raises(ValueError, match="Unknown stage"):
        resolve_setpoint("bogus", 24.0, 60.0)


@pytest.mark.parametrize("stage", [s for s in STAGES if s != "dry"])
def test_humidity_band_matches_the_stages_kpa_band(stage):
    leaf = round(22.5 - LEAF_TEMP_OFFSET_C, 1)
    low, high = sorted(humidity_range_bounds_for_stage(stage, 22.5, leaf))
    vpd_min, vpd_max = VPD_RANGES[stage]
    assert calculate_leaf_vpd(22.5, leaf, high) == pytest.approx(vpd_min, abs=0.02)
    assert calculate_leaf_vpd(22.5, leaf, low) == pytest.approx(vpd_max, abs=0.02)


def test_a_grow_band_with_a_vpd_range_replaces_the_constant():
    band = {"vpd_min": 0.5, "vpd_max": 0.7, "rh_min": None, "rh_max": None}
    setpoint = resolve_setpoint("early_veg", 24.0, 50.0, band=band)

    leaf = round(24.0 - LEAF_TEMP_OFFSET_C, 1)
    expected = sorted(humidity_range_bounds_for_stage("early_veg", 24.0, leaf, vpd_band=(0.5, 0.7)))
    assert setpoint.humidity_band == tuple(expected)


def test_a_grow_band_with_an_rh_range_wins_over_its_vpd_range():
    band = {"vpd_min": 0.5, "vpd_max": 0.7, "rh_min": 40.0, "rh_max": 45.0}
    setpoint = resolve_setpoint("late_veg", 24.0, 50.0, band=band)

    assert setpoint.humidity_band == (40.0, 45.0)
    assert setpoint.target_humidity == 42.5


def test_no_band_is_exactly_the_built_in_answer():
    assert resolve_setpoint("dry", 24.0, 62.0, band=None) == resolve_setpoint("dry", 24.0, 62.0)


def test_control_tuning_reads_the_environment(monkeypatch):
    from autocann.config import control_tuning_from_env

    monkeypatch.setenv("AUTOCANN_MIN_OFF_SECONDS", "300")
    monkeypatch.setenv("AUTOCANN_MIN_CHANGEOVER_SECONDS", "240")
    tuning = control_tuning_from_env()
    assert tuning.min_off_seconds == 300.0
    assert tuning.min_changeover_seconds == 240.0
    assert tuning.min_on_seconds == 60.0            # untouched default


@pytest.mark.parametrize("value", ["abc", "-5"])
def test_control_tuning_rejects_nonsense(monkeypatch, value):
    from autocann.config import control_tuning_from_env

    monkeypatch.setenv("AUTOCANN_MIN_ON_SECONDS", value)
    with pytest.raises(ValueError):
        control_tuning_from_env()


def test_leaf_vpd_takes_the_air_vapour_at_the_air_temperature():
    """
    Leaf VPD = SVP(leaf) - SVP(air) * RH/100. The humidity is a property of the
    air, so the vapour term must use the air's saturation pressure; the old
    formula used the leaf's and overstated the deficit.
    """
    air, leaf, rh = 25.0, 23.5, 60.0
    expected = saturation_vapor_pressure(leaf) - saturation_vapor_pressure(air) * rh / 100
    assert calculate_leaf_vpd(air, leaf, rh) == pytest.approx(round(expected, 2), abs=0.005)


def test_leaf_vpd_equals_air_vpd_when_the_leaf_is_at_air_temperature():
    for temperature in TEMPERATURES:
        for humidity in HUMIDITIES:
            assert calculate_leaf_vpd(temperature, temperature, humidity) == calculate_vpd(
                temperature, humidity
            )


@pytest.mark.parametrize("target", [0.4, 0.6, 0.8, 1.0, 1.2, 1.5, 1.6])
def test_humidity_for_leaf_vpd_round_trips_through_leaf_vpd(target):
    air, leaf = 25.0, 23.5
    humidity = calculate_humidity_for_leaf_vpd(air, leaf, target)
    assert calculate_leaf_vpd(air, leaf, humidity) == pytest.approx(target, abs=0.01)


def test_early_veg_band_at_25_c_with_the_leaf_offset_is_about_60_to_72_percent():
    low, high = sorted(
        humidity_range_bounds_for_stage("early_veg", 25.0, 25.0 - LEAF_TEMP_OFFSET_C)
    )
    assert low == pytest.approx(59.8, abs=0.1)
    assert high == pytest.approx(72.5, abs=0.1)


def test_humidity_band_without_a_leaf_temperature_is_the_air_only_band():
    for stage in STAGES:
        if stage == "dry":
            continue
        assert humidity_range_bounds_for_stage(stage, 24.0) == humidity_range_bounds_for_stage(
            stage, 24.0, 24.0
        )
        assert calculate_target_humidity(stage, 24.0) == calculate_target_humidity(
            stage, 24.0, 24.0
        )


def test_resolve_setpoint_band_is_the_leaf_band_at_the_air_temperature():
    setpoint = resolve_setpoint("early_veg", 25.0, 60.0)
    expected = sorted(
        humidity_range_bounds_for_stage("early_veg", 25.0, round(25.0 - LEAF_TEMP_OFFSET_C, 1))
    )
    assert setpoint.humidity_band == pytest.approx(tuple(expected))
    assert setpoint.leaf_vpd == calculate_leaf_vpd(25.0, setpoint.leaf_temperature, 60.0)
