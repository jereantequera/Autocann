from __future__ import annotations

import json

import pytest

from autocann.hardware.outputs import manual_override_key, output_names, parse_bool_flag


class FakeRedis:
    """Enough of redis-py for the endpoints, and it records TTLs."""

    def __init__(self):
        self.store = {}
        self.ttls = {}

    def get(self, key):
        value = self.store.get(key)
        return value.encode() if isinstance(value, str) else value

    def set(self, key, value, ex=None):
        self.store[key] = value
        self.ttls[key] = ex

    def delete(self, key):
        self.store.pop(key, None)
        self.ttls.pop(key, None)


@pytest.fixture
def client(monkeypatch, temp_db):
    import autocann.web.app as web

    fake = FakeRedis()
    monkeypatch.setattr(web.redis, "Redis", lambda **kwargs: fake)
    app = web.create_app()
    app.config.update(TESTING=True)
    test_client = app.test_client()
    test_client.redis = fake
    return test_client


def test_dead_redis_history_endpoint_is_gone(client):
    assert client.get("/api/historical-data").status_code == 404


def test_manual_override_is_published_with_an_expiry(client):
    response = client.post("/api/output-control", json={"name": "ventilation", "state": True})
    assert response.status_code == 200

    key = manual_override_key("ventilation")
    assert client.redis.store[key] == "true"
    # An override that never expires could leave a device running for days.
    assert client.redis.ttls[key] == 15 * 60


def test_manual_override_honours_a_custom_duration(client):
    client.post("/api/output-control", json={"name": "humidity_up", "state": True, "duration_seconds": 120})
    assert client.redis.ttls[manual_override_key("humidity_up")] == 120


def test_null_state_hands_the_output_back_to_automatic_control(client):
    client.post("/api/output-control", json={"name": "humidity_up", "state": True})
    client.post("/api/output-control", json={"name": "humidity_up", "state": None})
    assert manual_override_key("humidity_up") not in client.redis.store


@pytest.mark.parametrize(
    "payload, status",
    [
        ({}, 400),
        ({"name": "humidity_up"}, 200),                               # state omitted == clear
        ({"name": "humidity_up", "state": "on"}, 400),                 # not a boolean
        ({"name": "nope", "state": True}, 404),
        ({"name": "humidity_up", "state": True, "duration_seconds": 0}, 400),
        ({"name": "humidity_up", "state": True, "duration_seconds": 99999}, 400),
        ({"name": "humidity_up", "state": True, "duration_seconds": "abc"}, 400),
    ],
)
def test_manual_override_validates_its_input(client, payload, status):
    assert client.post("/api/output-control", json=payload).status_code == status


def test_output_status_reports_state_and_any_override(client):
    client.redis.set("humidity_control_up", "true")
    client.post("/api/output-control", json={"name": "humidity_down", "state": False})

    outputs = {o["name"]: o for o in client.get("/api/output-status").get_json()["outputs"]}
    assert set(outputs) == set(output_names())
    assert outputs["humidity_up"]["state"] is True
    assert outputs["humidity_up"]["manual_override"] is None
    assert outputs["humidity_down"]["manual_override"] is False
    assert outputs["ventilation"]["state"] is None


def test_esp32_posts_a_reading_and_it_comes_back(client):
    response = client.post("/api/sensor/indoor", json={"temperature": 24.0, "humidity": 60.0})
    assert response.status_code == 200
    assert response.get_json()["data"]["vpd"] == pytest.approx(1.19, abs=0.01)

    stored = json.loads(client.redis.store["esp32_indoor"])
    assert stored["source"] == "esp32"
    assert client.get("/api/sensor/indoor").get_json()["is_stale"] is False


@pytest.mark.parametrize(
    "payload, status",
    [
        ({"humidity": 60.0}, 400),
        ({"temperature": 24.0}, 400),
        ({"temperature": "warm", "humidity": 60.0}, 400),
        ({"temperature": 24.0, "humidity": 150.0}, 400),
        ({"temperature": 999.0, "humidity": 60.0}, 400),
    ],
)
def test_esp32_endpoint_rejects_bad_readings(client, payload, status):
    assert client.post("/api/sensor/indoor", json=payload).status_code == status


@pytest.mark.parametrize(
    "raw, expected",
    [
        (b"true", True), (b"TRUE", True), (b" on ", True), (b"1", True), (b"yes", True),
        (b"false", False), (b"0", False), (b"off", False), (b"no", False),
        (None, None), (b"maybe", None), (b"", None),
    ],
)
def test_redis_flag_parsing(raw, expected):
    assert parse_bool_flag(raw) is expected


def test_grow_lifecycle_through_the_api(client):
    created = client.post("/api/grows", json={"name": "Test", "stage": "flowering"})
    assert created.status_code == 201
    grow_id = created.get_json()["grow_id"]

    assert client.get("/api/grows/active").get_json()["name"] == "Test"
    assert client.put(f"/api/grows/{grow_id}/stage", json={"stage": "dry"}).status_code == 200
    assert client.get("/api/grows/active").get_json()["stage"] == "dry"
    assert client.post(f"/api/grows/{grow_id}/end").status_code == 200


@pytest.mark.parametrize(
    "payload, status",
    [({"name": "x", "stage": "bogus"}, 400), ({"stage": "flowering"}, 400)],
)
def test_grow_creation_validates_its_input(client, payload, status):
    assert client.post("/api/grows", json=payload).status_code == status
