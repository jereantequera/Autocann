from __future__ import annotations

import pytest

from autocann.cli.vpd import resolve_setpoint
from autocann.control.vpd_math import (
    STAGES,
    VPD_RANGES,
    calculate_vpd,
    humidity_range_bounds_for_stage,
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
    vpd_at_target = calculate_vpd(setpoint.leaf_temperature, setpoint.target_humidity)
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


def test_unknown_stage_is_rejected_rather_than_silently_accepted():
    with pytest.raises(ValueError, match="Unknown stage"):
        resolve_setpoint("bogus", 24.0, 60.0)


@pytest.mark.parametrize("stage", [s for s in STAGES if s != "dry"])
def test_humidity_band_matches_the_stages_kpa_band(stage):
    low, high = sorted(humidity_range_bounds_for_stage(stage, 22.5))
    vpd_min, vpd_max = VPD_RANGES[stage]
    assert calculate_vpd(22.5, high) == pytest.approx(vpd_min, abs=0.02)
    assert calculate_vpd(22.5, low) == pytest.approx(vpd_max, abs=0.02)


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
