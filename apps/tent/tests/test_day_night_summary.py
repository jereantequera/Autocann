"""
Day and night sub-summaries of the period summary.

The old fields of the summary are a contract: the stat tiles, the analytics
and the weekly report read them, and `fixtures/period_summary_snapshot.json`
pins their shape and values for a fixed dataset. The day and night
sub-summaries are added next to them, never instead of them.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from autocann.time import ARGENTINA_TZ

SNAPSHOT = Path(__file__).parent / "fixtures" / "period_summary_snapshot.json"

#: 2026-10-01 00:00 local. Three days of samples, one every five minutes.
T0 = int(ARGENTINA_TZ.localize(datetime(2026, 10, 1)).timestamp())
SAMPLES = 3 * 288
END = T0 + SAMPLES * 300


def build_dataset(temp_db):
    """
    A deterministic grow with the lights on from 06:00 to midnight. The day
    rows run warmer and drier-looking than the night ones, so a split shows.
    """
    grow = temp_db.create_grow("Prueba", "early_veg")
    for i in range(SAMPLES):
        ts = T0 + i * 300
        hour = datetime.fromtimestamp(ts, ARGENTINA_TZ).hour
        day = 6 <= hour < 24
        vpd = round(0.9 + (i % 6) * 0.1, 2) if day else round(0.5 + (i % 4) * 0.1, 2)
        sample = {
            "temperature": (24.0 + (i % 9) * 0.5) if day else (19.0 + (i % 7) * 0.5),
            "humidity": (62.0 + (i % 5)) if day else (55.0 + (i % 4)),
            "vpd": vpd,
            "outside_temperature": 17.0 + (i % 3),
            "outside_humidity": 60.0,
            "leaf_temperature": None,
            "leaf_vpd": round(vpd - 0.1, 2),
            "target_humidity": 60.0,
        }
        temp_db.store_sensor_sample(sample, grow_id=grow, at=ts)
    return grow


def legacy_fields(summary):
    """The keys the summary had before day and night existed."""
    return {key: value for key, value in summary.items() if key not in ("day", "night")}


def test_the_old_fields_match_the_snapshot(temp_db):
    grow = build_dataset(temp_db)
    summary = temp_db.get_period_summary(T0, END, grow)
    expected = json.loads(SNAPSHOT.read_text())
    assert json.dumps(legacy_fields(summary), sort_keys=True) == json.dumps(expected, sort_keys=True)


def test_the_summary_adds_day_and_night_beside_the_old_fields(temp_db):
    grow = build_dataset(temp_db)
    summary = temp_db.get_period_summary(T0, END, grow)

    assert set(legacy_fields(summary)) == set(json.loads(SNAPSHOT.read_text()))
    assert "day" in summary and "night" in summary


def test_the_default_photoperiod_splits_the_samples_six_to_midnight(temp_db):
    grow = build_dataset(temp_db)
    summary = temp_db.get_period_summary(T0, END, grow)

    # Three full days: 18 hours of light and 6 of dark each, five-minute samples.
    assert summary["day"]["sample_count"] == 3 * 18 * 12
    assert summary["night"]["sample_count"] == 3 * 6 * 12
    assert summary["day"]["sample_count"] + summary["night"]["sample_count"] == summary["sample_count"]


def test_day_and_night_averages_reflect_their_own_rows(temp_db):
    grow = build_dataset(temp_db)
    summary = temp_db.get_period_summary(T0, END, grow)

    assert summary["day"]["temperature"]["min"] == 24.0
    assert summary["night"]["temperature"]["max"] == 22.0
    assert summary["day"]["temperature"]["avg"] > summary["night"]["temperature"]["avg"]
    assert summary["day"]["humidity"]["avg"] > summary["night"]["humidity"]["avg"]


def test_a_saved_photoperiod_changes_the_split(temp_db):
    grow = build_dataset(temp_db)
    temp_db.set_photoperiod(grow, 6, 12)  # six hours of light

    summary = temp_db.get_period_summary(T0, END, grow)

    assert summary["day"]["sample_count"] == 3 * 6 * 12
    assert summary["night"]["sample_count"] == 3 * 18 * 12


def test_time_in_range_is_given_per_part_for_one_grow(temp_db):
    grow = build_dataset(temp_db)
    summary = temp_db.get_period_summary(T0, END, grow)

    for part in ("day", "night"):
        in_range = summary[part]["time_in_range"]
        assert in_range["total"] == summary[part]["sample_count"]
        assert 0 <= in_range["in_range"] <= in_range["total"]
        assert 0 <= in_range["percent"] <= 100


def test_all_grows_splits_each_row_by_its_own_photoperiod_and_gives_no_band(temp_db):
    grow = build_dataset(temp_db)  # 6 h of light is set below, so this grow is 6 + 18
    temp_db.set_photoperiod(grow, 6, 12)
    other = temp_db.create_grow("Otro", "early_veg")  # the default: 18 h of light
    for i in range(288):  # one day of samples
        temp_db.store_sensor_sample(
            {"temperature": 21.0, "humidity": 60.0, "vpd": 1.0}, grow_id=other, at=T0 + i * 300)

    summary = temp_db.get_period_summary(T0, END, temp_db.ALL_GROWS)

    # First grow: 3 days, 6 h light -> 216 day rows, 648 night rows.
    # Second grow: 1 day, 18 h light -> 216 day rows, 72 night rows.
    assert summary["sample_count"] == SAMPLES + 288
    assert summary["day"]["sample_count"] == 216 + 216
    assert summary["night"]["sample_count"] == 648 + 72
    assert summary["day"]["time_in_range"] is None
    assert summary["night"]["time_in_range"] is None


def test_an_empty_window_still_reads_exactly_as_before(temp_db):
    assert temp_db.get_period_summary(0, 3600) == {"sample_count": 0}
