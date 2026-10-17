"""
El detector de anomalías del histórico, sin base de datos.

Lo que se prueba es lo de #65: lo que se repite es un ítem con su cuenta y no
un ítem por fila, cada mensaje termina diciendo qué hacer, y lo que nadie puede
arreglar (una lectura imposible, un valor quieto) no es una alerta.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from autocann.control import anomalies as an
from autocann.control.anomalies import find_anomalies, when
from autocann.control.timing import SAMPLE_SAVE_INTERVAL_SECONDS
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


def test_an_old_impossible_row_is_not_an_alert_nor_two_spikes():
    """
    Lecturas imposibles ya no son una alerta (nadie puede hacer nada): se
    descartan en la fuente. Una fila vieja que quedó guardada no salta ni como
    alerta ni como dos cambios bruscos de 60 °C.
    """
    rows = _calm(10)
    rows[5]["temperature"] = 85.0  # 24 -> 85 -> 24 would be two 60 °C jumps
    rows[7]["humidity"] = 140.0
    report = find_anomalies(rows, rows[-1]["timestamp"] + 60)
    assert report == {"status": "ok", "anomalies": [], "warnings": []}


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
    rows += _rows([(24.0, 20.0), (24.0, 60.0), (40.0, 60.5)], start=rows[-1]["timestamp"] + STEP)
    rows += _rows([(22.0, 60 + i * 0.3) for i in range(7)], start=rows[-1]["timestamp"] + 30 * 60)
    report = find_anomalies(rows, rows[-1]["timestamp"] + an.MAX_GAP_SECONDS + 60)
    kinds = {i["type"] for i in _all(report)}
    # Every type the detector still raises; impossible and stuck values are gone.
    assert kinds == {"stale_data", "data_gap", "temperature_spike", "humidity_spike"}
    for item in _all(report):
        assert any(verb in item["message"] for verb in ("Revisá", "Mirá", "mirá", "fijate")), item
        assert {"type", "severity", "message", "timestamp"} <= item.keys()


def test_when_names_the_day_only_when_it_changes():
    assert when(T0, T0) == "a las 14:00"
    assert when(T0, T0 + 40 * 60) == "entre 14:00 y 14:40"
    assert when(T0, T0 + 24 * 3600) == "entre 15/10 14:00 y 16/10 14:00"


def test_a_value_that_does_not_move_is_not_an_alert():
    rows = _rows([(22.0, 60.0)] * 20)
    assert find_anomalies(rows, rows[-1]["timestamp"] + 60)["status"] == "ok"


def test_one_discarded_read_leaves_no_gap():
    """Descartar una fila deja 10 min entre vecinas: menos que el hueco de 15."""
    rows = _calm(10)
    del rows[4]
    assert _by_type(find_anomalies(rows, rows[-1]["timestamp"] + 60), "data_gap") == []


def test_a_fresh_grow_with_no_rows_is_awaiting_its_first_sample_not_no_data():
    report = find_anomalies([], T0, grow_started_at=T0 - 60)
    assert report["status"] == "ok"
    assert report["anomalies"] == []
    assert [i["type"] for i in report["warnings"]] == ["awaiting_first_sample"]
    item = report["warnings"][0]
    assert item["severity"] == "info"
    assert item["message"] == (
        "Todavía no se guardó ninguna muestra: la primera se guarda a los 5 minutos de arrancar.")


def test_the_awaiting_message_takes_its_minutes_from_the_save_interval(monkeypatch):
    monkeypatch.setattr(an, "SAMPLE_SAVE_INTERVAL_SECONDS", 120)
    monkeypatch.setattr(an, "FIRST_SAMPLE_GRACE_SECONDS", 240)
    report = find_anomalies([], T0, grow_started_at=T0 - 10)
    assert "a los 2 minutos de arrancar." in report["warnings"][0]["message"]


def test_a_grow_past_the_grace_window_with_no_rows_is_no_data_as_before():
    report = find_anomalies([], T0, grow_started_at=T0 - 20 * 60)
    assert report["status"] == "critical"
    assert [i["type"] for i in report["anomalies"]] == ["no_data"]
    assert report["warnings"] == []


def test_the_grace_window_ends_exactly_at_two_save_intervals():
    grace = an.FIRST_SAMPLE_GRACE_SECONDS
    assert grace == 2 * 5 * 60
    assert find_anomalies([], T0, grow_started_at=T0 - grace + 1)["status"] == "ok"
    at_the_edge = find_anomalies([], T0, grow_started_at=T0 - grace)
    assert [i["type"] for i in at_the_edge["anomalies"]] == ["no_data"]


def test_without_a_grow_start_no_rows_is_no_data_as_before():
    report = find_anomalies([], T0)
    assert [i["type"] for i in report["anomalies"]] == ["no_data"]


def test_rows_present_ignore_the_grow_start():
    rows = _calm(5)
    assert find_anomalies(rows, T0 + 60, grow_started_at=T0) == find_anomalies(rows, T0 + 60)


def test_the_loop_saves_on_the_interval_the_detector_assumes():
    """The grace window is two save intervals, so the loop must save on the shared one."""
    try:
        from autocann.runtime.loop import DB_SAVE_INTERVAL_SECONDS
    except ImportError:
        from autocann.cli.vpd import DB_SAVE_INTERVAL_SECONDS
    assert DB_SAVE_INTERVAL_SECONDS == SAMPLE_SAVE_INTERVAL_SECONDS
