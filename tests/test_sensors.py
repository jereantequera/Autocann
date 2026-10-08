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


@pytest.fixture
def paired(monkeypatch):
    """A loop configured with a controlling sensor and a witness watching it."""
    from autocann.config import IndoorSensors

    s = Sensors(use_esp32_indoor=True, indoor_sensors=IndoorSensors(primary="a", witness="b"))
    monkeypatch.setattr(s, "_read_dht22", lambda *a, **k: (None, None))
    return s


def _serve(sensors, monkeypatch, readings):
    """Answer each Redis key with its own reading, as two real sensors would."""
    monkeypatch.setattr(
        sensors, "_read_esp32_key", lambda key, label: readings.get(key, (None, None))
    )


def test_only_the_primary_drives_the_reading(paired, monkeypatch):
    _serve(paired, monkeypatch, {"esp32_indoor:a": (24.0, 60.0), "esp32_indoor:b": (26.0, 52.0)})
    data = paired.read()

    assert (data["temperature"], data["humidity"]) == (24.0, 60.0)
    assert paired.drift_report is not None


def test_a_dead_witness_does_not_trip_the_failsafe(paired, monkeypatch):
    """
    The witness drives nothing. If its silence aged the reading, a broken
    cross-check sensor would de-energise a tent that is being measured fine.
    """
    _serve(paired, monkeypatch, {"esp32_indoor:a": (24.0, 60.0)})
    data = paired.read()

    assert data["temperature"] == 24.0
    assert paired.reading_age_seconds < 1.0


def test_a_dead_primary_is_still_a_failsafe_even_with_a_live_witness(paired, monkeypatch):
    """The witness is not a spare: it is never promoted to drive control."""
    _serve(paired, monkeypatch, {"esp32_indoor:b": (24.0, 60.0)})

    assert paired.read() is None
    assert paired.reading_age_seconds == float("inf")


def test_sustained_divergence_is_surfaced(paired, monkeypatch):
    from autocann.control.drift import DriftConfig, DriftMonitor

    paired.drift = DriftMonitor(DriftConfig(baseline_samples=4, offset_samples=4, stuck_samples=50))

    for i in range(10):      # both healthy, a steady +1.0 °C / +2 % offset
        wobble = 0.1 * (i % 3)
        _serve(paired, monkeypatch, {
            "esp32_indoor:a": (24.0 + wobble, 60.0 + wobble),
            "esp32_indoor:b": (25.0 + wobble, 62.0 + wobble),
        })
        paired.read()
    assert paired.drift_report.ok

    for i in range(10):      # the primary's humidity walks away
        wobble = 0.1 * (i % 3)
        _serve(paired, monkeypatch, {
            "esp32_indoor:a": (24.0 + wobble, 48.0 + wobble),
            "esp32_indoor:b": (25.0 + wobble, 62.0 + wobble),
        })
        paired.read()

    assert paired.drift_report.diverged
    assert not paired.drift_report.ok


def test_a_single_sensor_installation_reads_only_the_legacy_key(monkeypatch):
    """Nothing changes until a witness is configured."""
    s = Sensors(use_esp32_indoor=True)
    monkeypatch.setattr(s, "_read_dht22", lambda *a, **k: (None, None))

    keys = []
    monkeypatch.setattr(s, "_read_esp32_key", lambda key, label: keys.append(key) or (24.0, 60.0))

    s.read()
    assert keys == ["esp32_indoor"]
    assert s.drift_report is None


def test_a_corrupt_stored_baseline_does_not_stop_startup(paired, monkeypatch):
    """Redis holding garbage must not keep the control loop from coming up."""
    import autocann.cli.vpd as vpd

    monkeypatch.setattr(vpd.redis_client, "get", lambda key: b"{not json")
    paired.load_drift_baseline()
    assert paired.drift.baseline is None


def test_an_unreachable_redis_does_not_stop_startup(paired, monkeypatch):
    import autocann.cli.vpd as vpd

    def boom(key):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(vpd.redis_client, "get", boom)
    paired.load_drift_baseline()
    assert paired.drift.baseline is None
