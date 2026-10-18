from __future__ import annotations

import random

from autocann.sim.nodes import (
    BOOT_REASON,
    DROPOUT_READS,
    FW_VERSION,
    HEARTBEAT_INTERVAL_SECONDS,
    REPORT_INTERVAL_SECONDS,
    SENSOR_NODE_IP,
    SENSOR_NODE_NAME,
    SENSOR_PINS,
    NodeStatus,
    attempt,
)


class Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _sensor_node(clock: Clock, dropout_chance: float = 0.0, seed: int = 1) -> NodeStatus:
    return NodeStatus(SENSOR_NODE_NAME, ip=SENSOR_NODE_IP, sensors=SENSOR_PINS, firmware=FW_VERSION,
                      rng=random.Random(seed), dropout_chance=dropout_chance, clock=clock)


def _texts(report: dict) -> list[str]:
    return [line["text"] for line in report["lines"]]


# ---------------------------------------------------------------------------
# Payload shape, as firmware/sensor_node sends it
# ---------------------------------------------------------------------------

REPORT_KEYS = {"node", "firmware", "uptime_ms", "ip", "rssi", "free_heap", "wifi_reconnects",
               "boot_reason", "sensors", "lines"}
SENSOR_KEYS = {"id", "pin", "reads_ok", "reads_failed", "consecutive_failed", "posts_ok",
               "posts_failed", "last_status"}
LINE_KEYS = {"ms", "level", "text"}


def test_sensor_report_has_the_firmware_fields_and_no_others():
    clock = Clock()
    node = _sensor_node(clock)
    node.boot_log()
    report = node.due_report()
    assert set(report) == REPORT_KEYS
    assert report["node"] == "sensores"
    assert report["firmware"] == "2026.10.12"
    assert report["boot_reason"] == BOOT_REASON


def test_sensor_report_types_and_units_match_the_firmware():
    clock = Clock()
    node = _sensor_node(clock)
    node.boot_log()
    clock.now += 12.5
    report = node.due_report()
    assert isinstance(report["uptime_ms"], int) and report["uptime_ms"] == 12_500
    assert isinstance(report["rssi"], int)
    assert isinstance(report["free_heap"], int)
    assert isinstance(report["wifi_reconnects"], int)
    assert report["ip"] == "192.168.0.50"
    assert [s["id"] for s in report["sensors"]] == ["a", "b"]
    assert [s["pin"] for s in report["sensors"]] == [32, 33]
    for sensor in report["sensors"]:
        assert set(sensor) == SENSOR_KEYS
        assert isinstance(sensor["id"], str)
        assert all(isinstance(sensor[k], int) for k in SENSOR_KEYS - {"id"})
    for line in report["lines"]:
        assert set(line) == LINE_KEYS
        assert line["level"] in ("info", "warn", "error")
        assert isinstance(line["ms"], int) and isinstance(line["text"], str)


# ---------------------------------------------------------------------------
# Boot lines and counters
# ---------------------------------------------------------------------------


def test_boot_lines_are_the_firmwares_strings():
    node = _sensor_node(Clock())
    node.boot_log()
    texts = _texts(node.due_report())
    assert texts[0] == "arranque 2026.10.12, motivo: encendido"
    assert texts[1].startswith("WiFi conectado, ip 192.168.0.50, senal ")
    assert texts[1].endswith(" dBm")


def test_rssi_is_plausible_around_minus_sixty():
    node = _sensor_node(Clock(), seed=7)
    values = [node.rssi() for _ in range(200)]
    assert all(-64 <= v <= -56 for v in values)
    assert len(set(values)) > 1


def test_successful_reads_and_posts_advance_the_counters():
    clock = Clock()
    node = _sensor_node(clock)
    for _ in range(3):
        assert node.read("a", 24.0, 60.0)
        node.posted("a", 200)
    report = node.due_report()
    a = report["sensors"][0]
    assert (a["reads_ok"], a["reads_failed"], a["posts_ok"], a["posts_failed"], a["last_status"]) == (3, 0, 3, 0, 200)
    assert report["sensors"][1]["reads_ok"] == 0


def test_a_dropout_fails_reads_logs_the_firmware_error_and_then_recovers():
    clock = Clock()
    node = _sensor_node(clock)
    node._sensors["a"].dropout_left = DROPOUT_READS  # start a dropout directly
    assert [node.read("a", 24.0, 60.0) for _ in range(DROPOUT_READS)] == [False] * DROPOUT_READS
    assert node.read("a", 24.1, 60.2) is True
    report = node.due_report()
    a = report["sensors"][0]
    assert (a["reads_failed"], a["reads_ok"], a["consecutive_failed"]) == (3, 1, 0)
    texts = _texts(report)
    assert "sensor a (GPIO 32): 3 lecturas fallidas seguidas" in texts
    assert "sensor a: volvio a leer despues de 3 fallas" in texts


def test_dropouts_are_drawn_only_when_asked_for():
    node = _sensor_node(Clock(), dropout_chance=0.0)
    assert all(node.read("b", 24.0, 60.0) for _ in range(500))


def test_a_rejected_post_counts_and_logs_with_its_status():
    node = _sensor_node(Clock())
    node.posted("a", 400, "sensor_id desconocido")
    node.posted("a", 200)
    report = node.due_report()
    a = report["sensors"][0]
    assert (a["posts_failed"], a["posts_ok"], a["last_status"]) == (1, 1, 200)
    texts = _texts(report)
    assert "sensor a: el POST volvio 400 sensor_id desconocido" in texts
    assert "sensor a: el backend vuelve a aceptar las lecturas" in texts


def test_attempt_returns_the_status_the_firmware_would_see():
    def refused():
        raise ValueError("body")

    assert attempt(lambda: None) == 200
    assert attempt(refused) == -1


# ---------------------------------------------------------------------------
# Cadence: report every 30 s, heartbeat every 5 min, log kept until accepted
# ---------------------------------------------------------------------------


def test_the_first_report_goes_out_at_once_then_every_30_seconds():
    clock = Clock()
    node = _sensor_node(clock)
    assert node.due_report() is not None
    clock.now += REPORT_INTERVAL_SECONDS - 1
    assert node.due_report() is None
    clock.now += 1
    assert node.due_report() is not None


def test_the_heartbeat_comes_every_five_minutes_with_the_last_readings():
    clock = Clock()
    node = _sensor_node(clock)
    node.read("a", 24.1, 60.3)
    node.due_report()
    clock.now += HEARTBEAT_INTERVAL_SECONDS
    texts = _texts(node.due_report())
    assert any(t.startswith("a: 24.1 C 60.3 % · b: sin lectura (senal ") for t in texts)


def test_lines_stay_until_the_backend_accepts_the_report():
    clock = Clock()
    node = _sensor_node(clock)
    node.boot_log()
    first = node.due_report()
    node.report_sent(500)
    clock.now += REPORT_INTERVAL_SECONDS
    second = node.due_report()
    assert _texts(second)[:2] == _texts(first)[:2]
    node.report_sent(200)
    clock.now += REPORT_INTERVAL_SECONDS
    assert _texts(node.due_report()) == ["el reporte de estado vuelve a llegar"]


def test_the_log_keeps_the_newest_lines_when_the_node_cannot_report():
    clock = Clock()
    node = _sensor_node(clock)
    for i in range(40):
        node.posted("b", 500, f"fallo {i}")
        node.report_sent(500)
    texts = _texts(node.due_report())
    assert len(texts) == 24
    assert texts[-1] == "sensor b: el POST volvio 500 fallo 39"


# ---------------------------------------------------------------------------
# API: the builders' payloads are what the backend stores and shows
# ---------------------------------------------------------------------------


def test_nodes_posted_by_the_simulator_show_up_in_the_api(client):
    clock = Clock()
    sensors = _sensor_node(clock, dropout_chance=0.0)
    sensors.boot_log()
    sensors.read("a", 24.0, 60.0)
    sensors.posted("a", 200)

    assert client.post("/api/node/log", json=sensors.due_report()).status_code == 200

    nodes = {n["node"]: n for n in client.get("/api/nodes").get_json()["nodes"]}
    assert set(nodes) == {"sensores"}
    assert nodes["sensores"]["state"] == "online"
    assert nodes["sensores"]["firmware"] == "2026.10.12"
    assert [s["reads_ok"] for s in nodes["sensores"]["sensors"]] == [1, 0]

    log = client.get("/api/node/log?node=sensores").get_json()
    assert log["lines"][0]["text"] == "arranque 2026.10.12, motivo: encendido"


def test_the_fake_relay_sends_no_status_and_no_log(monkeypatch):
    # Like firmware/relay_node, which only polls /api/outputs/desired.
    import threading
    import time

    from autocann.cli import simulate as sim
    from autocann.sim import fake_nodes

    calls = []

    def fake_http_json(method, url, body=None, timeout=3.0):
        calls.append((method, url))
        return {"outputs": {}, "failsafe": False}

    monkeypatch.setattr(fake_nodes, "http_json", fake_http_json)
    halt = threading.Event()
    relay = sim.FakeRelayNode("http://127.0.0.1:1", sim.TentWorld("drift"), halt)
    relay.start()
    time.sleep(0.3)
    halt.set()
    relay.join(timeout=5)
    assert calls and all(url.endswith("/api/outputs/desired") for _, url in calls)
