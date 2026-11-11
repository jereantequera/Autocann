"""
La placa que conmuta los relés, que no reporta nada y por eso se la daba por viva.

El 2026-11-10 a las 18:14 el nodo de relés dejó de pedir órdenes. Quince horas
después el panel decía «Un poco húmedo · deshumidificando», con «El
deshumidificador arrancó hace un momento», sobre un relé abierto que se veía a
simple vista; `/api/health` contestaba «Todo en orden · 2 sensores posteando».
Nada mentía: cada estado de salida es lo que el lazo *pidió*, y eso es lo que
estaba en pantalla. Lo que faltaba era alguien que notara que el pedido no
llegaba a ninguna parte.

El nodo de sensores postea su estado y se lo puede leer. Este no postea nada:
sólo hace `GET /api/outputs/desired` cada dos segundos. Esa consulta es toda la
evidencia que existe, así que ahora se anota, y de su antigüedad sale el
veredicto que prueban estos tests.
"""

from __future__ import annotations

import json

import pytest

from autocann.config import (
    RELAY_MODE_GPIO,
    RELAY_MODE_REMOTE,
    RELAY_NODE_LAST_POLL_KEY,
    RELAY_NODE_MAX_AGE_SECONDS,
)
from autocann.web.diagnostics import (
    history_verdict,
    loop_verdict,
    overall_verdict,
    relay_node_verdict,
    sensor_verdict,
)

NOW = 1_700_000_000
NAME = "ESP32 relés (192.168.100.77)"


def _verdict(last_poll, *, mode=RELAY_MODE_REMOTE, now=NOW):
    return relay_node_verdict(last_poll, now, mode=mode, name=NAME)


# ---------------------------------------------------------------------------
# El veredicto
# ---------------------------------------------------------------------------


def test_with_the_relays_on_the_raspberry_there_is_nothing_to_be_silent():
    """
    En modo gpio el lazo mueve sus propios pines: entre la decisión y el relé
    no hay nadie que pueda faltar, así que no hay veredicto que dar.
    """
    assert _verdict({"at": NOW - 99999}, mode=RELAY_MODE_GPIO) is None


def test_a_node_that_just_asked_is_switching():
    verdict = _verdict({"at": NOW - 2, "ip": "192.168.100.77"})
    assert verdict["state"] == "polling"
    assert verdict["level"] == "ok"
    assert verdict["ip"] == "192.168.100.77"
    assert verdict["age_seconds"] == 2


def test_a_node_that_stopped_asking_is_an_error_not_a_warning():
    """
    Error y no warn a propósito: mientras nadie conmuta, la carpa no está
    controlada. Es el mismo peso que el lazo caído, porque es el mismo efecto.
    """
    verdict = _verdict({"at": NOW - 15 * 3600, "ip": "192.168.100.77"})
    assert verdict["state"] == "silent"
    assert verdict["level"] == "error"
    assert verdict["age_seconds"] == 15 * 3600


def test_the_threshold_is_the_one_in_the_configuration():
    """
    Justo adentro sigue conmutando; un segundo más y no. El nodo consulta cada
    dos segundos, así que el umbral son quince consultas perdidas: ni un WiFi
    lento ni una respuesta demorada lo cruzan.
    """
    assert _verdict({"at": NOW - RELAY_NODE_MAX_AGE_SECONDS})["state"] == "polling"
    assert _verdict({"at": NOW - RELAY_NODE_MAX_AGE_SECONDS - 1})["state"] == "silent"


def test_a_node_that_never_asked_is_told_apart_from_one_that_stopped():
    """
    Las dos son errores y mandan a lugares distintos: la primera a la
    configuración y al firmware de la placa, la segunda a la alimentación y al
    WiFi de una placa que sí funcionó.
    """
    never = _verdict(None)
    assert never["state"] == "never"
    assert never["level"] == "error"
    assert never["age_seconds"] is None
    assert never["last_poll_at"] is None

    assert _verdict({"at": NOW - 99999})["state"] == "silent"


def test_an_unreadable_poll_counts_as_never_rather_than_as_recent():
    """Un registro roto no puede leerse como «acaba de consultar»."""
    assert _verdict({"ip": "192.168.100.77"})["state"] == "never"
    assert _verdict({"at": "cuando sea"})["state"] == "never"


def test_the_verdict_names_the_board_so_somebody_can_walk_up_to_it():
    assert NAME in _verdict({"at": NOW - 99999})["detail"]
    assert NAME in _verdict(None)["detail"]


# ---------------------------------------------------------------------------
# El titular
# ---------------------------------------------------------------------------


def _healthy_parts():
    sensor = {
        "label": "Sensor A", "role": "primary", "controls": True,
        "has_reading": True, "is_stale": False, "age_seconds": 2, "flags": [],
    }
    sensor = {**sensor, **sensor_verdict(sensor)}
    loop = loop_verdict({"timestamp": NOW}, 0, None, NOW, mode="active")
    history = history_verdict(60, loop["state"])
    return [sensor], loop, history


def test_everything_green_still_says_nobody_is_switching_the_relays():
    """
    El titular que faltaba. Con los sensores posteando, el lazo decidiendo y el
    histórico guardando, esta función contestaba «Todo en orden» — y lo
    contestó durante quince horas sobre una carpa que no controlaba nadie.
    """
    sensors, loop, history = _healthy_parts()
    silent = _verdict({"at": NOW - 15 * 3600})

    assert overall_verdict(sensors, loop, history, [])["headline"].startswith("Todo en orden")

    verdict = overall_verdict(sensors, loop, history, [], silent)
    assert verdict["level"] == "error"
    assert verdict["headline"] == "Nadie está conmutando los relés"
    assert NAME in verdict["detail"]


def test_a_polling_node_leaves_the_headline_alone():
    sensors, loop, history = _healthy_parts()
    verdict = overall_verdict(sensors, loop, history, [], _verdict({"at": NOW - 2}))
    assert verdict["level"] == "ok"
    assert verdict["headline"].startswith("Todo en orden")


def test_a_dead_loop_still_outranks_a_silent_node():
    """
    Si el lazo no publica, el nodo se queda sin órdenes por consecuencia. Decir
    primero lo segundo manda a revisar una placa que está perfecta.
    """
    sensors, _, history = _healthy_parts()
    down = loop_verdict(None, None, None, NOW)
    verdict = overall_verdict(sensors, down, history, [], _verdict({"at": NOW - 99999}))
    assert verdict["headline"] == "El lazo de control no está corriendo"


# ---------------------------------------------------------------------------
# Las dos puntas del endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setenv("AUTOCANN_RELAY_MODE", RELAY_MODE_REMOTE)
    monkeypatch.setenv("AUTOCANN_RELAY_NODE_NAME", NAME)


def test_the_poll_is_what_records_the_node_as_alive(client, remote):
    """
    La consulta del nodo es su único rastro, así que es la que deja la marca.
    """
    assert client.redis.store.get(RELAY_NODE_LAST_POLL_KEY) is None

    client.get("/api/outputs/desired?ir_ack=0")

    recorded = json.loads(client.redis.store[RELAY_NODE_LAST_POLL_KEY])
    assert isinstance(recorded["at"], int)


def test_a_node_answered_failsafe_is_still_a_node_that_is_listening(client, remote):
    """
    Sin estado deseado publicado la respuesta es failsafe — y aun así el nodo
    está ahí. Confundir «el lazo no publica» con «la placa no está» manda a
    caminar hasta la carpa por un servicio que se reinicia desde el teclado.
    """
    answer = client.get("/api/outputs/desired").get_json()
    assert answer["failsafe"] is True

    status = client.get("/api/output-status").get_json()
    assert status["relay_node"]["state"] == "polling"


def test_the_outputs_travel_with_whoever_would_apply_them(client, remote):
    """
    En la misma respuesta y no en dos endpoints: el estado de una salida es un
    pedido, y esto dice si hay alguien del otro lado. Dos consultas distintas
    podrían contestar cosas distintas, y ese hueco es justo la mentira que se
    está sacando de la pantalla.
    """
    payload = client.get("/api/output-status").get_json()
    assert "outputs" in payload
    assert payload["relay_node"]["state"] == "never"
    assert payload["relay_node"]["level"] == "error"


def test_without_a_remote_node_the_panel_is_told_there_is_nothing_to_qualify(
        client, monkeypatch):
    monkeypatch.setenv("AUTOCANN_RELAY_MODE", RELAY_MODE_GPIO)
    assert client.get("/api/output-status").get_json()["relay_node"] is None


def test_sistema_gets_the_board_apart_from_the_nodes_that_report(client, remote):
    """
    Afuera de `nodes` porque no entra en esa forma: no tiene firmware, ni
    uptime, ni heap, ni motivo de arranque — todo eso sale de un reporte, y
    esta placa no manda ninguno.
    """
    payload = client.get("/api/nodes").get_json()
    assert payload["nodes"] == []
    assert payload["relay_node"]["name"] == NAME


def test_the_health_answer_carries_it_too(client, remote):
    """
    En `/api/health` además de en `/api/output-status`, porque la línea de
    estado del panel lo lee de acá: el titular de Sistema y esa línea salen
    del mismo campo y no pueden terminar diciendo cosas distintas.

    El titular acá es el del lazo, que en un cliente de test no publica nada y
    gana por precedencia; el orden entre los dos lo prueban las funciones puras
    de más arriba.
    """
    health = client.get("/api/health").get_json()
    assert health["relay_node"]["state"] == "never"
    assert health["relay_node"]["level"] == "error"
    assert health["level"] == "error"
