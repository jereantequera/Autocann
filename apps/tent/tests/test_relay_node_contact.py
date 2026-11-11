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
from autocann.web.diagnostics.relay_node import BOOTS_KEPT

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


# ---------------------------------------------------------------------------
# Lo que el nodo cuenta de sí mismo
# ---------------------------------------------------------------------------


def test_the_firmware_from_before_reports_nothing_and_that_is_not_a_fault():
    """
    Toda placa grabada antes del 2026-11-11 manda la consulta pelada. Sigue
    siendo una placa que conmuta: lo que no se sabe es por qué arrancó, no si
    está. Cada dato ausente tiene que leerse «no se sabe» y nunca cero.
    """
    verdict = _verdict({"at": NOW - 2, "ip": "192.168.100.77"})

    assert verdict["state"] == "polling"
    assert verdict["boot_reason"] is None
    assert verdict["boot_reason_label"] is None
    assert verdict["uptime_seconds"] is None
    assert verdict["rssi"] is None
    assert verdict["wifi_reconnects"] is None


def test_the_boot_reason_arrives_translated():
    """
    El nodo manda un slug porque su informe viaja en un query string; la frase
    es la misma que usa el nodo de sensores para el mismo caso, así que las dos
    placas dicen lo mismo en pantalla.
    """
    verdict = _verdict({"at": NOW - 2, "boot_reason": "brownout"})

    assert verdict["boot_reason"] == "brownout"
    assert "alimentación" in verdict["boot_reason_label"]


def test_a_boot_reason_nobody_recognises_never_reaches_the_panel():
    """
    `GET /api/outputs/desired` no pide autenticación, así que lo que venga por
    ahí es la entrada de cualquiera hasta que coincida con un caso conocido.
    """
    verdict = _verdict({"at": NOW - 2, "boot_reason": "<script>alert(1)</script>"})

    assert verdict["boot_reason"] == "unknown"
    assert verdict["boot_reason_label"] == "desconocido"


def test_the_uptime_is_said_so_a_fresh_boot_is_visible_without_asking():
    verdict = _verdict({"at": NOW - 2, "uptime_seconds": 90})

    assert verdict["uptime_seconds"] == 90
    assert "De pie desde" in verdict["detail"]


def test_three_restarts_in_half_an_hour_is_a_node_that_is_rebooting():
    """
    El agujero que abrió el autoreinicio: un nodo que se reinicia cada cinco
    minutos vuelve a consultar dos segundos después, así que por antigüedad de
    la consulta se ve impecable mientras la carpa pierde el control en cada
    vuelta. Es como se ve un brownout.
    """
    boots = [NOW - 1500, NOW - 900, NOW - 300]
    verdict = _verdict({"at": NOW - 2, "uptime_seconds": 300, "boots": boots,
                        "boot_reason": "brownout"})

    assert verdict["state"] == "rebooting"
    assert verdict["level"] == "warn"
    assert verdict["reboots_recent"] == 3
    assert verdict["headline"] == "El nodo de relés se está reiniciando solo"
    assert "alimentación" in verdict["detail"]


def test_one_restart_is_not_a_loop():
    verdict = _verdict({"at": NOW - 2, "uptime_seconds": 300, "boots": [NOW - 300]})

    assert verdict["state"] == "polling"
    assert verdict["reboots_recent"] == 1


def test_restarts_older_than_the_window_stop_counting():
    """
    Si no, una placa que tuvo una mala noche hace tres días queda marcada para
    siempre y el aviso deja de querer decir algo.
    """
    old = [NOW - 90000, NOW - 89000, NOW - 88000]
    verdict = _verdict({"at": NOW - 2, "uptime_seconds": 88000, "boots": old})

    assert verdict["state"] == "polling"
    assert verdict["reboots_recent"] == 0


def test_a_node_with_no_contact_at_all_outranks_one_that_is_rebooting():
    """
    Los dos son la misma placa y piden trips distintos: a una hay que ir a
    prenderla, a la otra a mirarle la fuente. El titular no las puede mezclar.
    """
    silent = _verdict({"at": NOW - 15 * 3600})
    rebooting = _verdict({"at": NOW - 2, "uptime_seconds": 60,
                          "boots": [NOW - 1200, NOW - 600, NOW - 60]})

    assert silent["headline"] == "Nadie está conmutando los relés"
    assert rebooting["headline"] == "El nodo de relés se está reiniciando solo"


def test_a_rebooting_node_does_not_outrank_a_sensor_that_stopped_posting():
    """
    El titular se elige por posición y no por nivel, así que lo que se adelanta
    a un sensor primario roto tiene que ser peor que eso — y una placa que
    conmuta casi todo el tiempo no lo es. Un nodo SIN contacto sí.
    """
    sensors, loop, history = _healthy_parts()
    broken = {**sensors[0], "has_reading": False, "is_stale": False}
    broken = {**broken, **sensor_verdict(broken)}
    rebooting = _verdict({"at": NOW - 2, "uptime_seconds": 60,
                          "boots": [NOW - 1200, NOW - 600, NOW - 60]})

    verdict = overall_verdict([broken], loop, history, [], rebooting)
    assert "no está posteando" in verdict["headline"]

    silent = overall_verdict([broken], loop, history, [], _verdict({"at": NOW - 99999}))
    assert silent["headline"] == "Nadie está conmutando los relés"


def test_a_rebooting_node_is_still_said_when_nothing_else_is_wrong():
    sensors, loop, history = _healthy_parts()
    rebooting = _verdict({"at": NOW - 2, "uptime_seconds": 60,
                          "boots": [NOW - 1200, NOW - 600, NOW - 60]})

    verdict = overall_verdict(sensors, loop, history, [], rebooting)
    assert verdict["level"] == "warn"
    assert verdict["headline"] == "El nodo de relés se está reiniciando solo"


# ---------------------------------------------------------------------------
# Lo que se anota de la consulta
# ---------------------------------------------------------------------------


def _recorded(client):
    return json.loads(client.redis.store[RELAY_NODE_LAST_POLL_KEY])


def test_the_node_counts_of_itself_on_the_way_past(client, remote):
    """
    No tiene otro canal: un POST propio sería un segundo temporizador que puede
    desincronizarse del primero, y no desincronizarse es por lo que este nodo
    sólo consulta.
    """
    client.get("/api/outputs/desired?ir_ack=0&boot=brownout&up=120&rssi=-55&rc=2")

    recorded = _recorded(client)
    assert recorded["boot_reason"] == "brownout"
    assert recorded["uptime_seconds"] == 120
    assert recorded["rssi"] == -55
    assert recorded["wifi_reconnects"] == 2

    status = client.get("/api/output-status").get_json()["relay_node"]
    assert "alimentación" in status["boot_reason_label"]


def test_numbers_out_of_range_are_dropped_rather_than_drawn(client, remote):
    """
    El endpoint no pide autenticación. Un dato ausente se lee «no se sabe»,
    que es honesto; uno inventado se dibujaría como si fuera medido.
    """
    client.get("/api/outputs/desired?boot=nada&up=-5&rssi=999&rc=abc")

    recorded = _recorded(client)
    assert recorded["boot_reason"] is None
    assert recorded["uptime_seconds"] is None
    assert recorded["rssi"] is None
    assert recorded["wifi_reconnects"] is None


def test_the_first_poll_counts_as_a_boot_that_nobody_saw(client, remote):
    client.get("/api/outputs/desired?boot=poweron&up=30")

    assert len(_recorded(client)["boots"]) == 1


def test_an_uptime_that_goes_backwards_is_a_restart(client, remote):
    """
    Se mira el contador y no la hora de arranque calculada: el nodo cuenta en
    `millis()` y la Pi en reloj de pared, así que esa cuenta se corre un
    segundo entre consulta y consulta y un reinicio corto se esconde adentro
    de la deriva. Un contador que baja no se puede esconder.
    """
    client.get("/api/outputs/desired?boot=poweron&up=600")
    client.get("/api/outputs/desired?boot=poweron&up=610")
    assert len(_recorded(client)["boots"]) == 1

    client.get("/api/outputs/desired?boot=poweron&up=4")
    assert len(_recorded(client)["boots"]) == 2


def test_a_different_boot_reason_is_a_restart_too(client, remote):
    client.get("/api/outputs/desired?boot=poweron&up=600")
    client.get("/api/outputs/desired?boot=sw&up=601")

    assert len(_recorded(client)["boots"]) == 2


def test_the_list_of_boots_does_not_grow_without_bound(client, remote):
    """
    Esta clave se reescribe cada dos segundos, así que la única parte que podría
    costar algo es la lista.
    """
    for uptime in range(40, 0, -2):
        client.get(f"/api/outputs/desired?boot=sw&up={uptime}")

    assert len(_recorded(client)["boots"]) == BOOTS_KEPT


def test_the_old_firmware_does_not_lose_the_boots_already_known(client, remote):
    """
    Un deploy que vuelve a firmware viejo, o una placa reemplazada por otra sin
    regrabar: lo que ya se vio pasó igual.
    """
    client.get("/api/outputs/desired?boot=poweron&up=30")
    client.get("/api/outputs/desired")

    assert len(_recorded(client)["boots"]) == 1
    assert _recorded(client)["uptime_seconds"] is None
