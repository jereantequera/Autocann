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


# ---------------------------------------------------------------------------
# Gap markers
# ---------------------------------------------------------------------------


def _seed(db, grow_id, timestamps, **extra):
    conn = db._open()
    for ts in timestamps:
        conn.execute(
            "INSERT INTO sensor_data (grow_id, timestamp, datetime, temperature, humidity,"
            " vpd, outside_temperature, outside_humidity) VALUES (?, ?, '', 24, 50, 1.2, 15, 70)",
            (grow_id, ts),
        )
    conn.commit()
    conn.close()


def test_an_outage_comes_back_as_a_break_not_as_a_straight_line(client, temp_db):
    """
    Without a marker the chart joins the two sides of a six-hour hole, which
    reads as a smooth change rather than missing data.
    """
    import time

    grow_id = temp_db.get_active_grow()["id"]
    now = int(time.time())
    before = [now - 3600 - i * 300 for i in range(6)]      # steady samples
    after = [now - i * 300 for i in range(6)]              # steady samples, 1h later
    _seed(temp_db, grow_id, before + after)

    payload = client.get(f"/api/sensor-history?start={now - 7200}&end={now}").get_json()
    gaps = [p for p in payload["data"] if p.get("gap")]

    assert len(gaps) == 1
    assert gaps[0]["gap_seconds"] > 300 * 2
    assert "datetime" in gaps[0]                 # still gets an axis label
    assert "temperature" not in gaps[0]          # and plots as a hole


def test_a_continuous_series_gets_no_markers(client, temp_db):
    import time

    grow_id = temp_db.get_active_grow()["id"]
    now = int(time.time())
    _seed(temp_db, grow_id, [now - i * 300 for i in range(12)])

    payload = client.get(f"/api/sensor-history?start={now - 3600}&end={now}").get_json()
    assert not any(p.get("gap") for p in payload["data"])


def test_history_comes_back_in_chronological_order(client, temp_db):
    """The gap check needs it, and so do the charts."""
    import time

    grow_id = temp_db.get_active_grow()["id"]
    now = int(time.time())
    _seed(temp_db, grow_id, [now - i * 300 for i in range(10)])

    data = client.get(f"/api/sensor-history?start={now - 3600}&end={now}").get_json()["data"]
    stamps = [p["timestamp"] for p in data]
    assert stamps == sorted(stamps)


def test_the_aggregated_endpoint_marks_outages_too(client, temp_db):
    import time

    grow_id = temp_db.get_active_grow()["id"]
    now = int(time.time())
    day = 24 * 3600
    # Two clusters a few days apart, aggregated hourly.
    _seed(temp_db, grow_id, [now - i * 3600 for i in range(4)])
    _seed(temp_db, grow_id, [now - 5 * day - i * 3600 for i in range(4)])

    payload = client.get("/api/history/aggregated?days=7&interval=hourly").get_json()
    assert any(p.get("gap") for p in payload["data"])


def test_two_indoor_sensors_do_not_overwrite_each_other(client):
    """
    Before sensor_id existed, both sensors wrote one key and the loop saw
    whichever posted last, alternating between two tents' worth of readings.
    """
    client.post("/api/sensor/indoor", json={"temperature": 24.0, "humidity": 60.0, "sensor_id": "a"})
    client.post("/api/sensor/indoor", json={"temperature": 26.0, "humidity": 55.0, "sensor_id": "b"})

    assert json.loads(client.redis.store["esp32_indoor:a"])["temperature"] == 24.0
    assert json.loads(client.redis.store["esp32_indoor:b"])["temperature"] == 26.0
    # The unidentified key stays untouched, so existing firmware is unaffected.
    assert "esp32_indoor" not in client.redis.store


def test_an_unidentified_post_still_lands_on_the_legacy_key(client):
    client.post("/api/sensor/indoor", json={"temperature": 24.0, "humidity": 60.0})
    stored = json.loads(client.redis.store["esp32_indoor"])
    assert stored["sensor_id"] is None


def test_each_identified_sensor_gets_its_own_status_entry(client):
    client.post("/api/sensor/indoor", json={"temperature": 24.0, "humidity": 60.0, "sensor_id": "a"})
    status = json.loads(client.redis.store["sensor_status"])
    assert status["indoor:a"]["ok"] is True
    # The control loop owns plain "indoor"; the endpoint must not fight it for it.
    assert status["indoor"] == {}


@pytest.mark.parametrize(
    "sensor_id",
    [
        "a:b",            # a ':' would let a poster reach outside its namespace
        "../x",
        "a b",
        "",
        "A" * 20,
        7,
    ],
)
def test_the_endpoint_refuses_sensor_ids_it_cannot_put_in_a_key(client, sensor_id):
    """This endpoint has no authentication, so the id is validated, not sanitised."""
    response = client.post(
        "/api/sensor/indoor",
        json={"temperature": 24.0, "humidity": 60.0, "sensor_id": sensor_id},
    )
    assert response.status_code == 400
    assert client.redis.store == {}


def test_an_innocuous_looking_id_cannot_collide_with_another_key(client):
    """
    The 'esp32_indoor:' prefix is what makes ids safe, not the id itself: even
    naming a sensor after a key the dashboard trusts lands inside the namespace.
    """
    client.post(
        "/api/sensor/indoor",
        json={"temperature": 24.0, "humidity": 60.0, "sensor_id": "sensor_status"},
    )
    assert "esp32_indoor:sensor_status" in client.redis.store
    assert json.loads(client.redis.store["sensor_status"])["indoor:sensor_status"]["ok"] is True


def test_a_reading_can_be_read_back_per_sensor(client):
    client.post("/api/sensor/indoor", json={"temperature": 24.0, "humidity": 60.0, "sensor_id": "a"})

    assert client.get("/api/sensor/indoor?sensor_id=a").get_json()["temperature"] == 24.0
    assert client.get("/api/sensor/indoor?sensor_id=b").status_code == 404
    assert client.get("/api/sensor/indoor?sensor_id=a:b").status_code == 400
