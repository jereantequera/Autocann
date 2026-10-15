from __future__ import annotations

from dataclasses import replace
from time import monotonic, sleep

import pytest

from autocann.cli.vpd import (
    DHT_READ_ATTEMPTS,
    DHT_RETRY_DELAY_SECONDS,
    OUTDOOR_MAX_AGE_SECONDS,
    Sensors,
)
from autocann.control.humidity import IDLE, ControllerConfig, ControllerState, decide
from autocann.control.sampling import Calibration


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
    sensors.refresh_outdoor()
    data = _feed(sensors, monkeypatch, (24.0, 60.0))
    assert (data["outside_temperature"], data["outside_humidity"]) == (12.0, 80.0)


# ---------------------------------------------------------------------------
# The outdoor sensor, off the critical path
# ---------------------------------------------------------------------------


def test_a_dead_outdoor_sensor_does_not_stall_the_control_loop(sensors, monkeypatch):
    """
    A DHT22 that does not answer costs DHT_READ_ATTEMPTS attempts with a sleep
    between them. Read inline, that was ~2 s of every 3 s cycle spent on a
    number no decision uses, with the relays unsupervised meanwhile.
    """
    calls = []

    def slow(sensor, name):
        calls.append(name)
        sleep(DHT_RETRY_DELAY_SECONDS * DHT_READ_ATTEMPTS)
        return None, None

    monkeypatch.setattr(sensors, "_read_dht22", slow)

    started = monotonic()
    data = _feed(sensors, monkeypatch, (24.0, 60.0))
    elapsed = monotonic() - started

    assert calls == [], f"el lazo leyó el DHT22 en línea: {calls}"
    assert elapsed < 0.5
    # And the reading is still honest about having no outdoor value.
    assert data["outside_temperature"] is None
    assert data["outside_humidity"] is None


def test_the_outdoor_row_carries_both_the_raw_and_the_corrected_reading(sensors, monkeypatch):
    """A correction clamps humidity, so inverting it does not recover the raw."""
    sensors.outdoor_calibration = Calibration(temperature_offset=2.5, humidity_offset=-4.0)
    monkeypatch.setattr(sensors, "_read_dht22", lambda *a, **k: (12.0, 80.0))
    sensors.refresh_outdoor()

    data = _feed(sensors, monkeypatch, (24.0, 60.0))
    assert (data["outside_temperature_raw"], data["outside_humidity_raw"]) == (12.0, 80.0)
    assert (data["outside_temperature"], data["outside_humidity"]) == (14.5, 76.0)


def test_a_failed_read_keeps_the_last_outdoor_value_while_it_is_still_fresh(sensors, monkeypatch):
    """One missed checksum has not changed the weather."""
    monkeypatch.setattr(sensors, "_read_dht22", lambda *a, **k: (12.0, 80.0))
    sensors.refresh_outdoor()

    monkeypatch.setattr(sensors, "_read_dht22", lambda *a, **k: (None, None))
    sensors.refresh_outdoor()

    assert sensors.outdoor_reading() == (12.0, 80.0)
    assert sensors.outdoor_status()["ok"] is True


def test_a_cached_outdoor_reading_is_not_served_once_it_is_old(sensors, monkeypatch):
    """
    The cache exists so the loop never blocks, not so an old number can be
    shown as a current one.
    """
    monkeypatch.setattr(sensors, "_read_dht22", lambda *a, **k: (12.0, 80.0))
    sensors.refresh_outdoor()
    assert sensors.outdoor_reading() == (12.0, 80.0)

    cached = sensors._outdoor
    sensors._outdoor = replace(cached, at=cached.at - OUTDOOR_MAX_AGE_SECONDS - 1)

    assert sensors.outdoor_reading() == (None, None)
    data = _feed(sensors, monkeypatch, (24.0, 60.0))
    assert data["outside_temperature"] is None
    assert "outside_temperature_raw" not in data


def test_the_outdoor_status_reports_the_reading_not_the_device(sensors, monkeypatch):
    """
    It used to answer "is the object initialised", so a DHT22 that came up and
    then answered nothing for hours kept a green badge on screen.
    """
    assert sensors.outdoor_status()["ok"] is False
    assert sensors.outdoor_status()["error"]

    monkeypatch.setattr(sensors, "_read_dht22", lambda *a, **k: (12.0, 80.0))
    sensors.refresh_outdoor()
    assert sensors.outdoor_status() == {"ok": True, "error": None}


def test_the_published_status_carries_the_outdoor_verdict(sensors, monkeypatch):
    published = {}
    monkeypatch.setattr("autocann.cli.vpd._set_redis",
                        lambda key, value, ttl=None: published.update({key: value}))

    sensors.publish_status(indoor_ok=True)
    import json
    assert json.loads(published["sensor_status"])["outdoor"]["ok"] is False


def test_the_background_reader_fills_the_cache_and_stops(sensors, monkeypatch):
    """The thread is the only thing that touches the outdoor device in production."""
    monkeypatch.setattr("autocann.cli.vpd.OUTDOOR_READ_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(sensors, "_read_dht22", lambda *a, **k: (12.0, 80.0))
    monkeypatch.setattr(sensors, "init_outdoor_dht22", lambda: None)

    sensors.start_outdoor_reader()
    try:
        deadline = monotonic() + 2.0
        while sensors.outdoor_reading() == (None, None) and monotonic() < deadline:
            sleep(0.01)
        assert sensors.outdoor_reading() == (12.0, 80.0)
    finally:
        sensors.stop_outdoor_reader()

    assert not sensors._outdoor_thread.is_alive()


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
