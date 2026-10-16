"""
El detector de anomalías del histórico, sin base de datos.

Lo que se prueba es lo de #65: una ráfaga de lecturas malas es un ítem con su
cuenta y no un ítem por fila, cada mensaje termina diciendo qué hacer, y una
misma lectura rota no sale dos veces (como inválida y como salto).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from autocann.control import anomalies as an
from autocann.control.anomalies import find_anomalies, when
from autocann.time import ARGENTINA_TZ

#: 2026-10-15 14:00 local, a fixed clock.
T0 = int(ARGENTINA_TZ.localize(datetime(2026, 10, 15, 14, 0)).timestamp())
STEP = an.SAMPLE_INTERVAL_SECONDS


def _rows(values, start=T0):
    """Rows every 5 minutes from `start`, from (temperature, humidity) pairs."""
    return [{"timestamp": start + i * STEP, "temperature": t, "humidity": h}
            for i, (t, h) in enumerate(values)]


def _calm(n, start=T0):
    # Moves a little every sample so nothing reads as stuck.
    return _rows([(24 + (i % 3) * 0.1, 60 + (i % 2) * 0.5) for i in range(n)], start)


def _all(report):
    return report["anomalies"] + report["warnings"]


def _by_type(report, kind):
    return [i for i in _all(report) if i["type"] == kind]


def test_a_clean_history_is_ok():
    rows = _calm(20)
    report = find_anomalies(rows, rows[-1]["timestamp"] + 60)
    assert report == {"status": "ok", "anomalies": [], "warnings": []}


def test_a_burst_of_invalid_readings_is_one_item_with_a_count_and_a_range():
    rows = _calm(30)
    for row in rows[10:22]:
        row["temperature"] = 85.0
    rows[15]["temperature"] = -40.0
    report = find_anomalies(rows, rows[-1]["timestamp"] + 60)

    items = _by_type(report, "invalid_temperature")
    assert len(items) == 1
    item = items[0]
    assert item["count"] == 12
    assert item["first"].endswith("14:50:00")
    assert item["last"].endswith("15:45:00")
    assert item["timestamp"] == item["last"]
    assert item["message"].startswith("Temperatura inválida: 12 lecturas entre 14:50 y 15:45")
    assert "de -40.0 a 85.0 °C" in item["message"]
    assert item["message"].endswith(an.FIX_WIRING)


def test_a_single_invalid_reading_reads_as_one():
    rows = _calm(10)
    rows[4]["humidity"] = 140.0
    item = _by_type(find_anomalies(rows, rows[-1]["timestamp"] + 60), "invalid_humidity")[0]
    assert item["count"] == 1
    assert item["message"].startswith("Humedad inválida: 1 lectura a las 14:20 (140.0 %)")


def test_an_invalid_reading_is_critical_while_it_lasts_and_a_warning_once_it_is_old():
    rows = _calm(10)
    rows[-1]["temperature"] = 99.0
    now = rows[-1]["timestamp"] + 60
    assert _by_type(find_anomalies(rows, now), "invalid_temperature")[0]["severity"] == "critical"

    rows += _calm(an.ONGOING_SECONDS // STEP + 1, start=now)
    report = find_anomalies(rows, rows[-1]["timestamp"] + 60)
    assert _by_type(report, "invalid_temperature")[0]["severity"] == "warning"
    assert report["status"] == "warning"


def test_a_broken_read_is_an_invalid_value_and_not_also_two_spikes():
    rows = _calm(10)
    rows[5]["temperature"] = 85.0  # 24 -> 85 -> 24 would be two 60 °C jumps
    report = find_anomalies(rows, rows[-1]["timestamp"] + 60)
    assert _by_type(report, "temperature_spike") == []
    assert len(_by_type(report, "invalid_temperature")) == 1


def test_repeated_spikes_are_one_item_with_the_worst_jump():
    temps = [24.0, 36.0, 24.5, 24.6, 37.5, 24.4, 24.3, 24.2]
    rows = _rows([(t, 60 + i * 0.1) for i, t in enumerate(temps)])
    items = _by_type(find_anomalies(rows, rows[-1]["timestamp"] + 60), "temperature_spike")
    assert len(items) == 1
    item = items[0]
    # Two excursions, each up and straight back down: two, not four jumps.
    assert item["count"] == 2
    assert item["severity"] == "warning"
    assert item["change"] == pytest.approx(13.1)
    assert "2 veces entre 14:05 y 14:20" in item["message"]
    assert "hasta 13.1 °C en 5 min" in item["message"]
    assert "fijate que el sensor" in item["message"]


def test_a_step_that_stays_is_one_spike():
    rows = _rows([(24.0, 60.0), (24.1, 60.2), (36.0, 60.1), (36.1, 60.3), (36.0, 60.2)])
    item = _by_type(find_anomalies(rows, rows[-1]["timestamp"] + 60), "temperature_spike")[0]
    assert item["count"] == 1
    assert "1 vez a las 14:10" in item["message"]


def test_a_jump_across_a_gap_is_not_a_spike():
    rows = _rows([(24.0, 60.0), (24.1, 60.2)])
    rows.append({"timestamp": rows[-1]["timestamp"] + an.SPIKE_WINDOW_SECONDS + 1,
                 "temperature": 40.0, "humidity": 95.0})
    report = find_anomalies(rows, rows[-1]["timestamp"] + 60)
    assert _by_type(report, "temperature_spike") == []
    assert _by_type(report, "humidity_spike") == []


def test_gaps_are_one_item_naming_the_longest():
    rows = _calm(5)
    rows += _calm(5, start=rows[-1]["timestamp"] + 40 * 60)
    rows += _calm(5, start=rows[-1]["timestamp"] + 20 * 60)
    gaps = _by_type(find_anomalies(rows, rows[-1]["timestamp"] + 60), "data_gap")
    assert len(gaps) == 1
    assert gaps[0]["count"] == 2
    assert gaps[0]["gap_minutes"] == 40
    assert gaps[0]["message"].startswith("2 huecos sin datos entre")
    assert "el más largo, 40 min" in gaps[0]["message"]
    assert "Sistema" in gaps[0]["message"]


def test_a_stuck_value_is_reported_once_with_how_long_and_whether_it_still_is():
    rows = _calm(5)
    rows += _rows([(22.0, 60 + i) for i in range(8)], start=rows[-1]["timestamp"] + STEP)
    report = find_anomalies(rows, rows[-1]["timestamp"] + 60)
    item = _by_type(report, "stuck_temperature")[0]
    assert item["count"] == 8
    assert item["ongoing"] is True
    assert "clavada en 22.0 °C durante 35 min" in item["message"]
    assert item["message"].endswith(an.FIX_WIRING)

    rows += _calm(3, start=rows[-1]["timestamp"] + STEP)
    item = _by_type(find_anomalies(rows, rows[-1]["timestamp"] + 60), "stuck_temperature")[0]
    assert item["ongoing"] is False


def test_an_invalid_value_repeated_is_not_also_stuck():
    rows = _rows([(-999.0, 60 + i) for i in range(an.STUCK_SAMPLES + 2)])
    report = find_anomalies(rows, rows[-1]["timestamp"] + 60)
    assert _by_type(report, "stuck_temperature") == []
    assert _by_type(report, "invalid_temperature")[0]["count"] == an.STUCK_SAMPLES + 2


def test_five_identical_values_are_not_stuck():
    rows = _rows([(22.0, 60 + i) for i in range(an.STUCK_SAMPLES - 1)])
    assert _by_type(find_anomalies(rows, rows[-1]["timestamp"] + 60), "stuck_temperature") == []


def test_nothing_stored_says_so_and_sends_to_sistema():
    report = find_anomalies([], T0, hours=24)
    assert report["status"] == "critical"
    item = report["anomalies"][0]
    assert item["type"] == "no_data"
    assert item["message"] == "No hay datos guardados en las últimas 24 h. " + an.SEE_SYSTEM


def test_a_stale_history_is_critical_and_sends_to_sistema():
    rows = _calm(5)
    report = find_anomalies(rows, rows[-1]["timestamp"] + an.MAX_GAP_SECONDS + 60)
    item = _by_type(report, "stale_data")[0]
    assert item["severity"] == "critical"
    assert item["minutes_ago"] == 16
    assert item["message"] == "Último dato guardado hace 16 min. " + an.SEE_SYSTEM


def test_every_message_ends_with_what_to_do():
    rows = _calm(5)
    rows[1]["temperature"] = 80.0
    rows[2]["humidity"] = -5.0
    rows += _rows([(24.0, 20.0), (24.0, 60.0), (40.0, 60.5)], start=rows[-1]["timestamp"] + STEP)
    rows += _rows([(22.0, 60 + i * 0.3) for i in range(7)], start=rows[-1]["timestamp"] + 30 * 60)
    report = find_anomalies(rows, rows[-1]["timestamp"] + an.MAX_GAP_SECONDS + 60)
    kinds = {i["type"] for i in _all(report)}
    assert kinds == {"stale_data", "data_gap", "invalid_temperature", "invalid_humidity",
                     "temperature_spike", "humidity_spike", "stuck_temperature"}
    for item in _all(report):
        assert any(verb in item["message"] for verb in ("Revisá", "Mirá", "mirá", "fijate")), item
        assert {"type", "severity", "message", "timestamp"} <= item.keys()


def test_when_names_the_day_only_when_it_changes():
    assert when(T0, T0) == "a las 14:00"
    assert when(T0, T0 + 40 * 60) == "entre 14:00 y 14:40"
    assert when(T0, T0 + 24 * 3600) == "entre 15/10 14:00 y 16/10 14:00"
