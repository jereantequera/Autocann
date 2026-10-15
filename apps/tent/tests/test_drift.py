from __future__ import annotations

import pytest

from autocann.control.drift import (
    DIVERGED,
    PRIMARY,
    RAILED,
    STUCK,
    WITNESS,
    DriftConfig,
    DriftMonitor,
    Offset,
)


@pytest.fixture
def config():
    """Small windows, so a test does not have to push 400 samples."""
    return DriftConfig(baseline_samples=5, offset_samples=5, stuck_samples=6)


@pytest.fixture
def monitor(config):
    return DriftMonitor(config)


def _feed(monitor, n, primary, witness):
    """
    Push `n` cycles, wobbling both readings together.

    The wobble keeps the offset between them exactly constant while making sure
    neither value is literally frozen - a real sensor always jitters, and
    feeding two perfectly flat series would trip the stuck detector in tests
    that are about something else.
    """
    report = None
    for i in range(n):
        wobble = 0.1 * (i % 3)
        p = (primary[0] + wobble, primary[1] + wobble) if primary else None
        w = (witness[0] + wobble, witness[1] + wobble) if witness else None
        report = monitor.update(primary=p, witness=w)
    return report


def test_a_constant_offset_never_alarms(monitor):
    """
    The whole design rests on this: two sensors in different corners of a tent
    legitimately disagree, so an offset on its own must never be an alarm.
    """
    report = _feed(monitor, 20, (24.0, 60.0), (25.2, 64.0))
    assert monitor.baseline == Offset(temperature=pytest.approx(1.2), humidity=pytest.approx(4.0))
    assert not report.diverged
    assert report.ok


def test_the_baseline_is_only_learned_once_enough_pairs_have_been_seen(monitor, config):
    for _ in range(config.baseline_samples - 1):
        assert monitor.update(primary=(24.0, 60.0), witness=(25.0, 63.0)).baseline is None
    assert monitor.update(primary=(24.0, 60.0), witness=(25.0, 63.0)).baseline is not None


def test_an_offset_that_moves_is_divergence(monitor):
    _feed(monitor, 10, (24.0, 60.0), (24.5, 61.0))     # baseline: +0.5 °C, +1 %
    report = _feed(monitor, 10, (24.0, 60.0), (24.5, 69.0))  # humidity offset now +9 %

    assert report.diverged
    assert not report.ok
    assert any("se separaron" in m for m in report.messages)


def test_a_drift_within_tolerance_is_not_reported(monitor):
    _feed(monitor, 10, (24.0, 60.0), (24.5, 61.0))
    report = _feed(monitor, 10, (24.0, 60.0), (24.9, 64.0))  # +0.4 °C, +3 %: under both limits
    assert not report.diverged


def test_divergence_alone_blames_neither_sensor(monitor):
    """Two sensors disagreeing does not say which one is wrong, so both are named."""
    _feed(monitor, 10, (24.0, 60.0), (24.0, 60.0))
    report = _feed(monitor, 10, (24.0, 60.0), (24.0, 70.0))
    assert report.diverged
    assert set(report.suspects) == {PRIMARY, WITNESS}


def test_a_frozen_sensor_is_named_on_its_own(monitor, config):
    """A sensor repeating one value is identifiable, so divergence is attributed."""
    for i in range(config.stuck_samples):
        report = monitor.update(primary=(24.0, 60.0), witness=(24.0 + i * 0.1, 60.0 + i * 0.1))

    assert report.flags[PRIMARY] == (STUCK,)
    assert report.flags[WITNESS] == ()
    assert report.suspects == (PRIMARY,)


def test_humidity_pinned_against_the_rail_is_flagged(monitor, config):
    for i in range(config.stuck_samples):
        report = monitor.update(primary=(24.0 + i * 0.1, 100.0), witness=(24.0, 60.0))
    assert RAILED in report.flags[PRIMARY]


def test_a_brief_excursion_to_the_rail_is_not_a_fault(monitor, config):
    """Lights-off really does take a tent to 100 % RH; only a stuck rail is a fault."""
    for i in range(config.stuck_samples - 1):
        monitor.update(primary=(24.0 + i * 0.1, 100.0), witness=(24.0, 60.0))
    report = monitor.update(primary=(25.0, 82.0), witness=(24.0, 60.0))
    assert report.flags[PRIMARY] == ()


def test_a_baseline_is_not_learned_from_an_already_broken_sensor(config):
    """
    Otherwise the fault becomes the reference and the detector can never fire.
    """
    monitor = DriftMonitor(DriftConfig(baseline_samples=4, offset_samples=4, stuck_samples=3))
    for _ in range(20):
        report = monitor.update(primary=(24.0, 60.0), witness=(24.0, 60.0))
    assert report.flags[PRIMARY] == (STUCK,)
    assert monitor.baseline is None


def test_without_a_witness_nothing_diverges(monitor):
    report = _feed(monitor, 20, (24.0, 60.0), None)
    assert report.baseline is None
    assert not report.diverged
    assert report.offset is None


def test_the_baseline_survives_a_restart(monitor, config):
    _feed(monitor, 10, (24.0, 60.0), (25.0, 64.0))
    stored = monitor.to_dict()

    # A fresh process, as after a reboot: it must not re-learn the baseline from
    # a sensor that has been drifting for weeks.
    restarted = DriftMonitor(config)
    assert restarted.load(stored)
    assert restarted.baseline == monitor.baseline

    report = _feed(restarted, 10, (24.0, 60.0), (25.0, 72.0))
    assert report.diverged


@pytest.mark.parametrize("stored", [None, {}, {"baseline_temperature": "x"}, {"nope": 1}])
def test_a_corrupt_stored_baseline_is_ignored_not_raised(monitor, stored):
    """A bad Redis value must not stop the control loop from starting."""
    assert monitor.load(stored) is False
    assert monitor.baseline is None


def test_resetting_the_baseline_relearns_it(monitor):
    _feed(monitor, 10, (24.0, 60.0), (25.0, 64.0))
    monitor.reset_baseline()
    assert monitor.baseline is None

    _feed(monitor, 10, (24.0, 60.0), (24.2, 60.5))
    assert monitor.baseline == Offset(temperature=pytest.approx(0.2), humidity=pytest.approx(0.5))


def test_dropped_readings_do_not_count_as_a_frozen_value(monitor, config):
    """An intermittent sensor must not be able to hide behind a sparse window."""
    for _ in range(config.stuck_samples * 2):
        monitor.update(primary=None, witness=(24.0, 60.0))
    report = monitor.update(primary=None, witness=(24.0, 60.0))
    assert report.flags[PRIMARY] == ()
    assert report.flags[WITNESS] == (STUCK,)


def test_the_flag_names_are_what_the_report_uses(monitor):
    report = monitor.update(primary=(24.0, 60.0), witness=(24.0, 60.0))
    assert set(report.flags) == {PRIMARY, WITNESS}
    assert DIVERGED not in report.flags[PRIMARY]
