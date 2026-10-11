from __future__ import annotations

import pytest

from autocann.control.sampling import (
    QUALITY_DEGRADED,
    QUALITY_FAILSAFE,
    QUALITY_OK,
    Calibration,
    SampleAccumulator,
    insert_gaps,
)


def reading(temp, hum, **extra):
    base = {"temperature": temp, "humidity": hum, "vpd": 1.2,
            "leaf_temperature": temp - 1.5, "leaf_vpd": 1.1,
            "outside_temperature": 15.0, "outside_humidity": 70.0,
            "stage": "flowering", "indoor_source": "esp32", "target_humidity": 50.5}
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# SampleAccumulator
# ---------------------------------------------------------------------------


def test_min_is_never_above_the_average_and_the_average_never_above_max():
    acc = SampleAccumulator()
    for temp, hum in [(22.0, 48.0), (26.0, 55.0), (24.0, 51.0), (25.0, 60.0)]:
        acc.push(reading(temp, hum))
    row = acc.build()

    assert row["temperature_min"] <= row["temperature"] <= row["temperature_max"]
    assert row["humidity_min"] <= row["humidity"] <= row["humidity_max"]
    assert (row["temperature_min"], row["temperature_max"]) == (22.0, 26.0)
    assert row["temperature"] == pytest.approx(24.25)


def test_sample_n_counts_the_readings_behind_the_row():
    """The whole point of the aggregate: the row says how much it stands for."""
    acc = SampleAccumulator()
    for i in range(100):
        acc.push(reading(24.0 + i * 0.01, 50.0))
    assert acc.build()["sample_n"] == 100
    assert acc.count == 100


def test_a_spike_shows_up_in_max_instead_of_being_thrown_away():
    """
    Storing one instantaneous reading per interval loses a 30-second excursion
    entirely, or stores it as if it were the whole period.
    """
    acc = SampleAccumulator()
    for _ in range(99):
        acc.push(reading(24.0, 50.0))
    acc.push(reading(31.0, 50.0))          # one brief excursion

    row = acc.build()
    assert row["temperature_max"] == 31.0
    assert row["temperature"] == pytest.approx(24.07, abs=0.01)   # barely moves the mean


def test_an_empty_interval_produces_no_row_at_all():
    """A gap in the history is the truth; a row of zeros would be a fabrication."""
    assert SampleAccumulator().build() is None


def test_an_interval_of_only_failed_reads_produces_no_row():
    acc = SampleAccumulator()
    for _ in range(5):
        acc.push(None, quality=QUALITY_FAILSAFE)
    assert acc.build() is None
    assert acc.count == 0


def test_a_missing_outdoor_sensor_does_not_poison_the_indoor_averages():
    acc = SampleAccumulator()
    acc.push(reading(24.0, 50.0))
    acc.push(reading(26.0, 52.0, outside_temperature=None, outside_humidity=None))

    row = acc.build()
    assert row["temperature"] == 25.0
    assert row["outside_temperature"] == 15.0      # the one reading that existed
    assert row["sample_n"] == 2


def test_quality_keeps_the_worst_seen_in_the_interval():
    acc = SampleAccumulator()
    acc.push(reading(24.0, 50.0), quality=QUALITY_OK)
    acc.push(reading(24.0, 50.0), quality=QUALITY_DEGRADED)
    acc.push(reading(24.0, 50.0), quality=QUALITY_OK)
    assert acc.build()["quality"] == QUALITY_DEGRADED

    acc.push(None, quality=QUALITY_FAILSAFE)
    assert acc.build()["quality"] == QUALITY_FAILSAFE


def test_context_fields_come_from_the_last_reading_that_had_them():
    acc = SampleAccumulator()
    acc.push(reading(24.0, 50.0, stage="late_veg", target_humidity=63.0))
    acc.push(reading(24.0, 50.0, stage="flowering", target_humidity=50.5))
    row = acc.build()
    assert row["stage"] == "flowering"
    assert row["target_humidity"] == 50.5     # a setpoint, not something to average


def test_the_stored_action_is_the_one_the_controller_spent_most_of_the_interval_in():
    acc = SampleAccumulator()
    for _ in range(8):
        acc.push(reading(24.0, 50.0), control_action="humidify")
    for _ in range(2):
        acc.push(reading(24.0, 50.0), control_action="idle")
    assert acc.build()["control_action"] == "humidify"


def test_reset_clears_everything_for_the_next_interval():
    acc = SampleAccumulator()
    acc.push(reading(30.0, 90.0), quality=QUALITY_FAILSAFE, control_action="dehumidify")
    acc.reset()
    assert acc.build() is None

    acc.push(reading(20.0, 40.0))
    row = acc.build()
    assert (row["temperature"], row["sample_n"], row["quality"]) == (20.0, 1, QUALITY_OK)
    assert "control_action" not in row


def test_booleans_are_not_mistaken_for_numbers():
    acc = SampleAccumulator()
    acc.push(reading(24.0, 50.0, vpd=True))
    assert "vpd" not in acc.build()


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


def test_an_offset_is_applied_to_both_measurements():
    cal = Calibration(temperature_offset=-1.2, humidity_offset=3.0)
    assert cal.apply(25.0, 50.0) == (23.8, 53.0)


def test_correction_is_reversible_from_the_corrected_value():
    cal = Calibration(temperature_offset=-1.2, humidity_offset=3.0)
    corrected = cal.apply(25.0, 50.0)
    assert cal.invert(*corrected) == pytest.approx((25.0, 50.0))


def test_an_identity_calibration_changes_nothing():
    cal = Calibration()
    assert cal.is_identity
    assert cal.apply(24.0, 60.0) == (24.0, 60.0)


def test_missing_readings_pass_through_uncorrected():
    cal = Calibration(temperature_offset=5.0, humidity_offset=5.0)
    assert cal.apply(None, None) == (None, None)
    assert cal.apply(20.0, None) == (25.0, None)


def test_a_correction_cannot_push_humidity_out_of_its_physical_range():
    cal = Calibration(humidity_offset=20.0)
    assert cal.apply(24.0, 95.0)[1] == 100.0
    assert Calibration(humidity_offset=-20.0).apply(24.0, 5.0)[1] == 0.0


def test_clamped_corrections_are_why_the_raw_value_is_stored():
    """
    Inversion cannot recover a reading the clamp swallowed, which is the reason
    the raw values go into the row instead of being recomputed on read.
    """
    cal = Calibration(humidity_offset=20.0)
    corrected = cal.apply(24.0, 95.0)
    assert cal.invert(*corrected)[1] != 95.0


# ---------------------------------------------------------------------------
# insert_gaps
# ---------------------------------------------------------------------------


def series(*offsets, step=300):
    return [{"timestamp": o, "temperature": 24.0} for o in offsets]


def test_an_evenly_spaced_series_is_left_alone():
    points = series(0, 300, 600, 900)
    assert insert_gaps(points, 300) == points


def test_an_outage_gets_a_marker_so_the_line_breaks():
    points = series(0, 300, 22_000, 22_300)       # ~6 hours missing
    out = insert_gaps(points, 300)
    gaps = [p for p in out if p.get("gap")]
    assert len(gaps) == 1
    assert gaps[0]["gap_seconds"] == 21_700
    assert 300 < gaps[0]["timestamp"] < 22_000     # sits inside the hole


def test_the_marker_separates_the_two_sides_rather_than_replacing_them():
    """No real sample may be dropped: the marker is inserted between them."""
    points = series(0, 10_000)
    out = insert_gaps(points, 300)
    assert len(out) == 3
    assert out[0]["timestamp"] == 0 and out[2]["timestamp"] == 10_000
    assert out[1]["gap"] is True


def test_a_small_hiccup_is_not_treated_as_an_outage():
    # One missed sample at 2x the interval is not past the threshold.
    assert not any(p.get("gap") for p in insert_gaps(series(0, 600), 300))
    assert any(p.get("gap") for p in insert_gaps(series(0, 601), 300))


def test_the_tolerance_factor_is_adjustable():
    points = series(0, 1200)
    assert not any(p.get("gap") for p in insert_gaps(points, 300, factor=5))
    assert any(p.get("gap") for p in insert_gaps(points, 300, factor=2))


def test_several_outages_each_get_their_own_marker():
    points = series(0, 300, 20_000, 20_300, 50_000)
    assert len([p for p in insert_gaps(points, 300) if p.get("gap")]) == 2


@pytest.mark.parametrize("points", [[], [{"timestamp": 0}]])
def test_a_series_too_short_to_have_a_gap(points):
    assert insert_gaps(points, 300) == points


def test_points_without_a_timestamp_are_passed_through():
    points = [{"temperature": 24.0}, {"temperature": 25.0}]
    assert insert_gaps(points, 300) == points


@pytest.mark.parametrize("interval, factor", [(0, 2), (-300, 2), (300, 0)])
def test_a_nonsensical_threshold_disables_the_check_instead_of_erroring(interval, factor):
    points = series(0, 99_999)
    assert insert_gaps(points, interval, factor=factor) == points
