"""
El veredicto único de estado, y el log de los nodos.

Lo que se prueba acá es sobre todo que dos partes de la pantalla no puedan decir
cosas distintas sobre lo mismo: ese fue el defecto que originó el módulo — un
cartel rojo de "sensor posiblemente desconectado" arriba de dos tarjetas verdes
de "OK", con los sensores posteando perfecto y el sistema en pausa.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from autocann.config import OBSERVER, PRIMARY, WITNESS, indoor_sensor_key
from autocann.time import ARGENTINA_TZ
from autocann.web.diagnostics import (
    LOG_MAX_LINE_LENGTH,
    LOG_MAX_LINES,
    LOG_MAX_LINES_PER_REPORT,
    append_log_lines,
    clean_log_lines,
    history_verdict,
    loop_verdict,
    node_verdict,
    reframe_history_anomalies,
    sensor_verdict,
    worst,
)


def _now():
    return int(datetime.now(ARGENTINA_TZ).timestamp())


# ---------------------------------------------------------------------------
# Un sensor
# ---------------------------------------------------------------------------


def test_a_sensor_that_never_posted_is_not_the_same_as_one_that_stopped():
    """
    Las dos fallas se ven iguales en una tarjeta que sólo sabe ok/error, y mandan
    a lugares distintos: una al id del firmware y al cableado, la otra a la red.
    """
    never = sensor_verdict({"role": PRIMARY, "has_reading": False})
    stopped = sensor_verdict({"role": PRIMARY, "has_reading": True, "is_stale": True,
                              "age_seconds": 900})

    assert never["state"] == "missing"
    assert stopped["state"] == "stale"
    assert never["action"] != stopped["action"]


def test_a_dead_primary_is_an_error_and_a_dead_witness_is_a_warning():
    """
    El primario que se calla deja al lazo sin nada sobre lo que decidir. El
    testigo que se calla es una pérdida de visibilidad, no una emergencia.
    """
    assert sensor_verdict({"role": PRIMARY, "has_reading": False})["level"] == "error"
    assert sensor_verdict({"role": WITNESS, "has_reading": False})["level"] == "warn"
    assert sensor_verdict({"role": OBSERVER, "has_reading": False})["level"] == "warn"


def test_a_fresh_reading_with_a_drift_flag_is_not_green():
    """El sensor contesta, a tiempo y en rango, y está equivocado: ese es el caso."""
    verdict = sensor_verdict({
        "role": PRIMARY, "has_reading": True, "is_stale": False,
        "age_seconds": 4, "flags": ["stuck"],
    })
    assert verdict["state"] == "suspect"
    assert verdict["level"] == "warn"


def test_a_fresh_reading_is_green():
    verdict = sensor_verdict({"role": PRIMARY, "has_reading": True, "is_stale": False,
                              "age_seconds": 4, "flags": []})
    assert (verdict["state"], verdict["level"], verdict["action"]) == ("posting", "ok", None)


# ---------------------------------------------------------------------------
# El lazo y el histórico
# ---------------------------------------------------------------------------


def test_a_paused_loop_is_not_reported_as_a_dead_loop():
    """
    Si no, alguien va a reiniciar un servicio que está detenido a propósito —
    y el lazo en pausa también deja de publicar.
    """
    now = _now()
    verdict = loop_verdict(None, None, now - 3600, now)
    assert verdict["state"] == "paused"
    assert verdict["level"] == "warn"
    assert verdict["paused_seconds"] == pytest.approx(3600, abs=2)


def test_a_loop_that_stopped_publishing_is_an_error():
    now = _now()
    assert loop_verdict({"timestamp": now}, 120, None, now)["state"] == "down"
    assert loop_verdict(None, None, None, now)["level"] == "error"


def test_a_loop_publishing_now_is_running():
    now = _now()
    assert loop_verdict({"timestamp": now}, 3, None, now)["state"] == "running"


def test_a_gap_in_the_history_while_paused_is_expected_not_broken():
    """El hueco es a propósito. Pintarlo de rojo enseña a ignorar el rojo."""
    verdict = history_verdict(8 * 3600, "paused")
    assert verdict["state"] == "paused"
    assert verdict["level"] == "ok"


def test_a_gap_in_the_history_with_the_loop_running_is_broken():
    verdict = history_verdict(8 * 3600, "running")
    assert verdict["state"] == "stalled"
    assert verdict["level"] == "error"


def test_worst_takes_the_most_serious_level():
    assert worst("ok", "warn", "error") == "error"
    assert worst("ok", "warn") == "warn"
    assert worst() == "ok"
    assert worst(None, "ok") == "ok"


# ---------------------------------------------------------------------------
# Las anomalías, releídas contra lo que pasa ahora
# ---------------------------------------------------------------------------


REPORT = {
    "status": "critical",
    "anomalies": [{
        "type": "stale_data",
        "severity": "critical",
        "message": "Último dato hace 238 minutos - sensor posiblemente desconectado",
    }],
    "warnings": [{"type": "data_gap", "severity": "warning", "message": "Sin datos por 869 minutos"}],
}

POSTING = {"state": "posting", "controls": True}
DEAD = {"state": "missing", "controls": True}


def test_a_pause_explains_the_gap_so_the_banner_says_nothing():
    """
    El defecto exacto que originó esto: con el sistema en pausa no se guardan
    filas a propósito, y eso salía como "sensor posiblemente desconectado" en
    rojo arriba de dos tarjetas verdes. Peor todavía desde que la pausa no vence
    sola: el número crece indefinidamente y el cartel se vuelve permanente.
    """
    out = reframe_history_anomalies(
        REPORT, {"loop": {"state": "paused"}, "sensors": [POSTING]})

    assert out["anomalies"] == []
    assert out["warnings"] == []
    assert out["status"] == "ok"


def test_a_live_sensor_is_never_called_disconnected():
    """Con el sensor posteando, el que no está guardando es el lazo, y eso dice."""
    out = reframe_history_anomalies(
        REPORT, {"loop": {"state": "running"}, "sensors": [POSTING]})

    message = out["anomalies"][0]["message"]
    assert "los sensores están posteando" in message
    assert out["anomalies"][0]["about"] == "history"


def test_a_gap_that_already_closed_is_not_answered_with_the_state_of_right_now():
    """
    Un `data_gap` es un hueco *entre* dos filas guardadas: ya pasó, y decir
    "pero los sensores están posteando" contesta con el estado de ahora una
    pregunta sobre anoche. El texto original no acusa a nadie y se deja.
    """
    out = reframe_history_anomalies(
        REPORT, {"loop": {"state": "running"}, "sensors": [POSTING]})

    gap = out["warnings"][0]
    assert gap["message"] == "Sin datos por 869 minutos"
    assert gap["about"] == "history"


def test_a_real_outage_is_left_alone():
    """Sin filas y sin lecturas, el sensor sí está desconectado: no se toca."""
    out = reframe_history_anomalies(
        REPORT, {"loop": {"state": "running"}, "sensors": [DEAD]})

    assert out["anomalies"] == REPORT["anomalies"]
    assert out["status"] == "critical"


def test_anomalies_that_are_not_about_the_history_are_left_alone():
    report = {
        "status": "critical",
        "anomalies": [{"type": "impossible_value", "message": "Temperatura de 95 °C"}],
        "warnings": [],
    }
    out = reframe_history_anomalies(
        report, {"loop": {"state": "paused"}, "sensors": [POSTING]})
    assert out["anomalies"] == report["anomalies"]
    assert out["status"] == "critical"


def test_the_banner_colour_is_recomputed_when_items_are_dropped():
    """Dejar "critical" sin nada crítico adentro mantiene el cartel rojo vacío."""
    out = reframe_history_anomalies(
        REPORT, {"loop": {"state": "paused"}, "sensors": [POSTING]})
    assert out["status"] != "critical"


# ---------------------------------------------------------------------------
# El log del nodo
# ---------------------------------------------------------------------------


def test_a_log_line_is_dated_from_the_nodes_own_clock():
    """
    El ESP32 cuenta milisegundos desde que arrancó, que es el único reloj que
    tiene. La hora de pared la pone el único que sabe las dos cosas.
    """
    received = 1_700_000_000
    lines = clean_log_lines(
        [{"ms": 50_000, "level": "warn", "text": "sensor b: lectura fallida"}],
        received, uptime_ms=110_000)

    assert lines[0]["at"] == received - 60


def test_a_line_without_a_clock_lands_at_the_time_it_arrived():
    lines = clean_log_lines([{"text": "hola"}], 1_700_000_000, uptime_ms=None)
    assert lines[0]["at"] == 1_700_000_000


def test_log_lines_are_bounded_in_every_direction():
    """El endpoint no tiene autenticación: todo lo que entra viene acotado."""
    raw = [{"text": "x" * 500, "level": "pánico"} for _ in range(500)]
    lines = clean_log_lines(raw, _now(), 1000)

    assert len(lines) == LOG_MAX_LINES_PER_REPORT
    assert all(len(line["text"]) <= LOG_MAX_LINE_LENGTH for line in lines)
    assert all(line["level"] == "info" for line in lines)


def test_empty_and_malformed_lines_are_dropped_not_stored():
    kept = clean_log_lines(["", {"text": "   "}, 7, None, "va", {"text": "venís"}], 1_700_000_000, 0)

    assert [line["text"] for line in kept] == ["va", "venís"]
    assert clean_log_lines("no es una lista", 1_700_000_000, 0) == []


def test_the_kept_log_drops_the_oldest_lines_first():
    stored = [{"text": f"vieja {i}"} for i in range(LOG_MAX_LINES)]
    out = append_log_lines(stored, [{"text": "nueva"}])

    assert len(out) == LOG_MAX_LINES
    assert out[-1]["text"] == "nueva"
    assert out[0]["text"] == "vieja 1"


def test_a_node_that_never_reported_says_so_instead_of_looking_dead():
    """El firmware viejo no reporta, y eso se ve igual que un nodo muerto."""
    verdict = node_verdict(None, _now())
    assert verdict["state"] == "unknown"
    assert "firmware" in verdict["detail"]


def test_a_node_that_answers_with_a_failing_sensor_is_not_simply_online():
    """Un nodo sano con un DHT22 que no lee es justo lo que hay que ver."""
    now = _now()
    verdict = node_verdict(
        {"received_at": now, "sensors": [{"id": "b", "consecutive_failed": 7}]}, now)
    assert verdict["state"] == "degraded"
    assert verdict["level"] == "warn"


def test_a_node_that_went_quiet_is_an_error():
    now = _now()
    assert node_verdict({"received_at": now - 600, "sensors": []}, now)["state"] == "silent"


# ---------------------------------------------------------------------------
# Los endpoints
# ---------------------------------------------------------------------------


def _post_reading(client, sensor_id, temperature=24.0, humidity=60.0):
    return client.post("/api/sensor/indoor",
                       json={"temperature": temperature, "humidity": humidity,
                             "sensor_id": sensor_id})


def test_health_answers_for_the_configured_sensors_even_without_readings(client, monkeypatch):
    monkeypatch.setenv("AUTOCANN_INDOOR_PRIMARY", "a")
    monkeypatch.setenv("AUTOCANN_INDOOR_WITNESS", "b")

    data = client.get("/api/health").get_json()

    assert [s["sensor_id"] for s in data["sensors"]] == ["a", "b"]
    assert all(s["state"] == "missing" for s in data["sensors"])
    assert data["level"] == "error"


def test_health_and_the_sensor_cards_cannot_disagree(client, monkeypatch):
    """
    Las dos cosas que se contradecían en pantalla salen ahora del mismo objeto:
    el badge del hero se calcula del mismo veredicto que las tarjetas.
    """
    monkeypatch.setenv("AUTOCANN_INDOOR_PRIMARY", "a")
    monkeypatch.delenv("AUTOCANN_INDOOR_WITNESS", raising=False)
    _post_reading(client, "a")

    data = client.get("/api/health").get_json()
    sensor = data["sensors"][0]

    assert sensor["state"] == "posting"
    assert sensor["controls"] is True
    assert data["loop"]["state"] == "down"   # no hay lazo publicando en el test
    assert data["level"] == "error"
    assert data["headline"] == "El lazo de control no está corriendo"


def test_health_reports_the_pause_rather_than_a_dead_loop(client, monkeypatch):
    monkeypatch.setenv("AUTOCANN_INDOOR_PRIMARY", "a")
    _post_reading(client, "a")
    client.redis.store["test_mode_since"] = str(_now() - 120)

    data = client.get("/api/health").get_json()

    assert data["loop"]["state"] == "paused"
    assert data["history"]["state"] == "paused"
    assert data["level"] == "warn"


def test_the_anomalies_endpoint_serves_the_reframed_report(client, monkeypatch):
    """
    El arreglo tiene que vivir del lado del servidor: /api/anomalies lo consume
    el banner, pero también cualquier otra cosa que lo mire.
    """
    monkeypatch.setenv("AUTOCANN_INDOOR_PRIMARY", "a")
    _post_reading(client, "a")
    client.redis.store["test_mode_since"] = str(_now() - 7200)

    data = client.get("/api/anomalies").get_json()

    assert data["status"] != "critical"
    assert not any("desconectado" in i["message"] for i in data["anomalies"])


def test_a_node_report_is_stored_and_read_back(client):
    response = client.post("/api/node/log", json={
        "node": "sensores",
        "firmware": "2026.10.12",
        "uptime_ms": 65_000,
        "rssi": -62,
        "sensors": [{"id": "a", "pin": 32, "reads_ok": 10, "reads_failed": 1}],
        "lines": [{"ms": 60_000, "level": "error", "text": "sensor b: lectura fallida"}],
    })
    assert response.get_json() == {"success": True, "accepted_lines": 1}

    log = client.get("/api/node/log?node=sensores").get_json()
    assert log["lines"][0]["text"] == "sensor b: lectura fallida"
    assert log["status"]["firmware"] == "2026.10.12"
    assert log["status"]["state"] == "online"
    assert log["nodes"] == ["sensores"]


def test_the_log_accumulates_across_reports(client):
    for i in range(3):
        client.post("/api/node/log",
                    json={"node": "sensores", "uptime_ms": 1000, "lines": [{"text": f"linea {i}"}]})

    lines = client.get("/api/node/log").get_json()["lines"]
    assert [line["text"] for line in lines] == ["linea 0", "linea 1", "linea 2"]


def test_the_single_node_does_not_have_to_be_named(client):
    client.post("/api/node/log", json={"node": "sensores", "lines": [{"text": "hola"}]})
    assert client.get("/api/node/log").get_json()["node"] == "sensores"


def test_a_log_request_without_a_node_says_which_ones_exist(client):
    answer = client.get("/api/node/log")
    assert answer.status_code == 400
    assert answer.get_json()["nodes"] == []


@pytest.mark.parametrize("node", ["a:b", "../x", "a b", "", "A" * 20, 7, None])
def test_a_node_name_that_cannot_go_in_a_key_is_refused(client, node):
    """Mismo motivo que con los sensor_id: el endpoint no tiene autenticación."""
    response = client.post("/api/node/log", json={"node": node, "lines": [{"text": "x"}]})
    assert response.status_code == 400
    assert client.redis.store == {}


def test_a_sensor_id_the_node_invents_does_not_reach_a_key(client):
    """Un id inválido se descarta, y uno válido vive adentro del reporte, no en una clave propia."""
    client.post("/api/node/log", json={
        "node": "sensores",
        "sensors": [{"id": "sensor_status"}, {"id": "mal:id"}],
    })
    stored = json.loads(client.redis.store["node_status:sensores"])

    assert [s["id"] for s in stored["sensors"]] == ["sensor_status"]
    assert set(client.redis.store) == {"node_status:sensores", "node_index"}


def test_the_nodes_endpoint_lists_what_has_reported(client):
    client.post("/api/node/log", json={"node": "sensores"})
    client.post("/api/node/log", json={"node": "reles"})

    nodes = client.get("/api/nodes").get_json()["nodes"]
    assert sorted(node["node"] for node in nodes) == ["reles", "sensores"]


def test_health_carries_the_nodes_so_the_panel_can_name_the_silent_one(client, monkeypatch):
    monkeypatch.setenv("AUTOCANN_INDOOR_PRIMARY", "a")
    _post_reading(client, "a")
    client.post("/api/node/log", json={"node": "sensores"})

    data = client.get("/api/health").get_json()
    assert [node["node"] for node in data["nodes"]] == ["sensores"]


def test_a_reading_posted_under_a_sensor_id_is_the_one_health_reads(client, monkeypatch):
    """El veredicto sale de la misma clave en la que el endpoint deja la lectura."""
    monkeypatch.setenv("AUTOCANN_INDOOR_PRIMARY", "a")
    _post_reading(client, "a", temperature=21.5)

    assert indoor_sensor_key("a") in client.redis.store
    assert client.get("/api/health").get_json()["sensors"][0]["temperature"] == 21.5
