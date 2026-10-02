from __future__ import annotations

import pytest

from autocann.cli.vpd import Sensors
from autocann.control.humidity import IDLE, ControllerConfig, ControllerState, decide


@pytest.fixture
def sensors(monkeypatch):
    s = Sensors(use_esp32_indoor=True)
    monkeypatch.setattr(s, "_read_dht22", lambda *a, **k: (None, None))
    return s


def _feed(sensors, monkeypatch, reading):
    monkeypatch.setattr(sensors, "_read_esp32_indoor", lambda: reading)
    return sensors.read()


def test_a_good_read_is_reported_as_fresh(sensors, monkeypatch):
    data = _feed(sensors, monkeypatch, (24.0, 60.0))
    assert data["temperature"] == 24.0
    assert sensors.reading_age_seconds < 1.0


def test_age_is_infinite_before_any_successful_read(sensors):
    assert sensors.reading_age_seconds == float("inf")


def test_a_failed_read_ages_even_though_the_median_still_has_a_value(sensors, monkeypatch):
    """
    MedianFilter.push(None) keeps returning its previous median, so read() still
    produces numbers after the sensor dies. Only the age reveals that, and the
    failsafe depends on it.
    """
    _feed(sensors, monkeypatch, (24.0, 60.0))
    monkeypatch.setattr(sensors, "last_reading_at", sensors.last_reading_at - 600)

    data = _feed(sensors, monkeypatch, (None, None))
    assert data is not None and data["temperature"] == 24.0   # stale value still returned
    assert sensors.reading_age_seconds > 500                  # but it is visibly old

    config = ControllerConfig(stale_after_seconds=120.0)
    state = ControllerState()
    decision = decide(
        humidity=data["humidity"], target_humidity=50.0, now=1000.0,
        state=state, config=config, reading_age_seconds=sensors.reading_age_seconds,
    )
    assert (decision.action, decision.reason) == (IDLE, "stale_data")


def test_a_missing_outdoor_sensor_is_left_empty_not_copied_from_indoor(sensors, monkeypatch):
    """The old loop copied the indoor values, drawing a fake outdoor curve."""
    data = _feed(sensors, monkeypatch, (24.0, 60.0))
    assert data["outside_temperature"] is None
    assert data["outside_humidity"] is None


def test_outdoor_values_are_used_when_available(sensors, monkeypatch):
    monkeypatch.setattr(sensors, "_read_dht22", lambda *a, **k: (12.0, 80.0))
    data = _feed(sensors, monkeypatch, (24.0, 60.0))
    assert (data["outside_temperature"], data["outside_humidity"]) == (12.0, 80.0)


def test_median_filtering_smooths_a_spiky_sensor(sensors, monkeypatch):
    for reading in [(24.0, 60.0), (24.1, 60.5), (23.9, 59.5), (24.0, 60.2)]:
        _feed(sensors, monkeypatch, reading)
    data = _feed(sensors, monkeypatch, (24.0, 95.0))      # one bogus humidity spike
    assert data["humidity"] == pytest.approx(60.2, abs=0.5)
