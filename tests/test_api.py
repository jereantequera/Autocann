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


# ---------------------------------------------------------------------------
# Every indoor sensor at once, for the dashboard
# ---------------------------------------------------------------------------


@pytest.fixture
def two_sensors(monkeypatch):
    """The installed configuration: 'a' drives control, 'b' only watches it."""
    monkeypatch.setenv("AUTOCANN_INDOOR_PRIMARY", "a")
    monkeypatch.setenv("AUTOCANN_INDOOR_WITNESS", "b")


def _store_indoor(client, key, temperature, humidity, age_seconds=0, vpd=1.0):
    """
    Seed a reading of a chosen age, which posting cannot do: the endpoint stamps
    its own time.
    """
    from datetime import datetime

    from autocann.time import ARGENTINA_TZ

    now = int(datetime.now(ARGENTINA_TZ).timestamp())
    client.redis.store[key] = json.dumps(
        {
            "temperature": temperature,
            "humidity": humidity,
            "vpd": vpd,
            "timestamp": now - age_seconds,
            "datetime": "2026-10-11 20:00:00",
        }
    )


def test_both_indoor_sensors_are_listed_with_their_roles(client, two_sensors):
    """
    Which ids exist and which one controls is server-side configuration, so the
    dashboard cannot discover the second sensor on its own.
    """
    client.post("/api/sensor/indoor", json={"temperature": 24.0, "humidity": 60.0, "sensor_id": "a"})
    client.post("/api/sensor/indoor", json={"temperature": 24.4, "humidity": 58.8, "sensor_id": "b"})

    body = client.get("/api/sensors/indoor").get_json()

    assert [(s["role"], s["sensor_id"]) for s in body["sensors"]] == [
        ("primary", "a"), ("witness", "b"),
    ]
    assert [s["temperature"] for s in body["sensors"]] == [24.0, 24.4]
    assert all(s["has_reading"] and not s["is_stale"] for s in body["sensors"])


def test_the_difference_is_witness_minus_primary(client, two_sensors):
    """The direction the drift monitor measures in, so the signs agree with it."""
    _store_indoor(client, "esp32_indoor:a", 24.0, 60.0)
    _store_indoor(client, "esp32_indoor:b", 24.5, 58.0)

    assert client.get("/api/sensors/indoor").get_json()["delta"] == {
        "temperature": 0.5, "humidity": -2.0,
    }


def test_a_stale_sensor_is_flagged_and_withholds_the_difference(client, two_sensors):
    """
    Subtracting an hour-old reading from a live one measures the clock, not the
    sensors, and on a dashboard that difference reads as a fault in the pair.
    """
    _store_indoor(client, "esp32_indoor:a", 24.0, 60.0)
    _store_indoor(client, "esp32_indoor:b", 24.5, 58.0, age_seconds=3600)

    body = client.get("/api/sensors/indoor").get_json()
    witness = body["sensors"][1]

    assert witness["is_stale"] is True
    assert witness["age_seconds"] >= 3600
    assert body["delta"] is None


def test_a_sensor_that_never_posted_is_not_reported_as_fresh(client, two_sensors):
    """Defaulting the other way would paint an empty card green."""
    _store_indoor(client, "esp32_indoor:a", 24.0, 60.0)

    witness = client.get("/api/sensors/indoor").get_json()["sensors"][1]

    assert (witness["has_reading"], witness["is_stale"]) == (False, True)
    assert witness["temperature"] is None


def test_each_sensor_carries_its_own_drift_flags(client, two_sensors):
    """
    Divergence says the pair disagrees; a per-sensor flag says which one to
    distrust. One shared alarm would throw that away.
    """
    _store_indoor(client, "esp32_indoor:a", 24.0, 60.0)
    _store_indoor(client, "esp32_indoor:b", 24.5, 58.0)
    client.redis.store["sensor_status"] = json.dumps(
        {
            "drift": {
                "ok": False,
                "suspects": ["witness"],
                "messages": ["sensor witness: valor congelado"],
                "flags": {"primary": [], "witness": ["stuck"]},
            }
        }
    )

    body = client.get("/api/sensors/indoor").get_json()

    assert body["sensors"][0]["flags"] == []
    assert body["sensors"][1]["flags"] == ["stuck"]
    assert body["drift"]["ok"] is False


@pytest.fixture
def primary_and_observer(monkeypatch):
    """
    What this tent actually has: 'a' controls, 'b' sits above the lamp and is
    only read.
    """
    monkeypatch.setenv("AUTOCANN_INDOOR_PRIMARY", "a")
    monkeypatch.delenv("AUTOCANN_INDOOR_WITNESS", raising=False)
    monkeypatch.setenv("AUTOCANN_INDOOR_OBSERVERS", "b")


def test_an_observer_is_shown_but_never_compared(client, primary_and_observer):
    """
    An observer is not a witness. Comparing against one measures where it sits,
    not whether either sensor is lying.
    """
    _store_indoor(client, "esp32_indoor:a", 20.6, 78.0)
    _store_indoor(client, "esp32_indoor:b", 21.5, 81.7)

    body = client.get("/api/sensors/indoor").get_json()

    assert [(s["role"], s["sensor_id"]) for s in body["sensors"]] == [
        ("primary", "a"), ("observer", "b"),
    ]
    assert body["has_witness"] is False
    assert body["delta"] is None


def test_several_sensors_are_not_a_cross_check(client, primary_and_observer):
    """
    The dashboard used to decide by counting cards, which called two sensors a
    cross-check whatever they were.
    """
    body = client.get("/api/sensors/indoor").get_json()
    assert len(body["sensors"]) == 2 and body["has_witness"] is False


def test_an_observer_cannot_also_be_the_primary(client, monkeypatch):
    """Two roles for one sensor means two meanings for one reading."""
    monkeypatch.setenv("AUTOCANN_INDOOR_PRIMARY", "a")
    monkeypatch.delenv("AUTOCANN_INDOOR_WITNESS", raising=False)
    monkeypatch.setenv("AUTOCANN_INDOOR_OBSERVERS", "a,b")

    response = client.get("/api/sensors/indoor")
    assert response.status_code == 500
    assert "a" in response.get_json()["error"]


def test_a_single_sensor_installation_answers_with_one_entry(client, monkeypatch):
    """The legacy install is not a special case the frontend has to handle."""
    monkeypatch.delenv("AUTOCANN_INDOOR_PRIMARY", raising=False)
    monkeypatch.delenv("AUTOCANN_INDOOR_WITNESS", raising=False)
    monkeypatch.delenv("AUTOCANN_INDOOR_OBSERVERS", raising=False)
    client.post("/api/sensor/indoor", json={"temperature": 24.0, "humidity": 60.0})

    body = client.get("/api/sensors/indoor").get_json()

    assert len(body["sensors"]) == 1
    assert body["sensors"][0]["redis_key"] == "esp32_indoor"
    assert body["delta"] is None


def test_a_misconfigured_pair_says_so_instead_of_dropping_a_sensor(client, monkeypatch):
    """Answering with a plausible one-sensor list is how a witness disappears."""
    monkeypatch.setenv("AUTOCANN_INDOOR_PRIMARY", "a")
    monkeypatch.setenv("AUTOCANN_INDOOR_WITNESS", "a")

    response = client.get("/api/sensors/indoor")

    assert response.status_code == 500
    assert "inválida" in response.get_json()["error"]


# ---------------------------------------------------------------------------
# Desired outputs, polled by the remote relay node
# ---------------------------------------------------------------------------


def _publish_desired(client, outputs, age_seconds=0):
    from datetime import datetime

    from autocann.time import ARGENTINA_TZ

    now = int(datetime.now(ARGENTINA_TZ).timestamp())
    client.redis.store["desired_outputs"] = json.dumps(
        {"outputs": outputs, "ts": now - age_seconds}
    )


ALL_OFF = {"humidity_up": False, "humidity_down": False, "ventilation": False}


def test_a_fresh_desired_state_is_served_as_is(client):
    _publish_desired(client, {"humidity_up": True, "humidity_down": False, "ventilation": True})
    body = client.get("/api/outputs/desired").get_json()

    assert body["failsafe"] is False
    assert body["outputs"] == {
        "humidity_up": True, "humidity_down": False, "ventilation": True,
    }


def test_a_stale_desired_state_answers_everything_off(client):
    """
    The loop's own failsafe cannot help once the loop is what stopped. A node
    faithfully applying the last command would keep a humidifier running.
    """
    _publish_desired(client, {"humidity_up": True}, age_seconds=600)
    body = client.get("/api/outputs/desired").get_json()

    assert body["failsafe"] is True
    assert body["outputs"] == ALL_OFF
    assert "stale" in body["reason"]


def test_no_published_state_answers_everything_off(client):
    body = client.get("/api/outputs/desired").get_json()
    assert (body["failsafe"], body["outputs"]) == (True, ALL_OFF)


def test_a_corrupt_desired_state_answers_everything_off(client):
    client.redis.store["desired_outputs"] = "{not json"
    body = client.get("/api/outputs/desired").get_json()
    assert (body["failsafe"], body["outputs"]) == (True, ALL_OFF)


def test_an_unreachable_redis_answers_everything_off(client, monkeypatch):
    def boom(key):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(client.redis, "get", boom)
    body = client.get("/api/outputs/desired").get_json()
    assert (body["failsafe"], body["outputs"]) == (True, ALL_OFF)


def test_an_output_missing_from_the_payload_defaults_to_off(client):
    """The node must never have to guess, so every name is always answered."""
    _publish_desired(client, {"humidity_up": True})
    body = client.get("/api/outputs/desired").get_json()

    assert body["outputs"] == {
        "humidity_up": True, "humidity_down": False, "ventilation": False,
    }


def test_the_answer_never_omits_an_output(client):
    _publish_desired(client, {})
    assert set(client.get("/api/outputs/desired").get_json()["outputs"]) == set(ALL_OFF)


# ---------------------------------------------------------------------------
# Test mode
# ---------------------------------------------------------------------------


def _now():
    from datetime import datetime

    from autocann.time import ARGENTINA_TZ

    return int(datetime.now(ARGENTINA_TZ).timestamp())


def test_test_mode_is_off_until_it_is_asked_for(client):
    body = client.get("/api/test-mode").get_json()
    assert body["active"] is False
    assert body["until"] is None


def test_entering_test_mode_stores_a_deadline_not_a_flag(client):
    """
    A flag someone forgets survives restarts and leaves the tent uncontrolled
    for days. A deadline heals itself.
    """
    body = client.post("/api/test-mode", json={"enabled": True}).get_json()

    assert body["active"] is True
    assert body["until"] >= _now()
    # The Redis TTL is the same expiry said a second way, so losing one still
    # ends the mode.
    assert client.redis.ttls["test_mode_until"] == 8 * 3600


def test_entering_test_mode_does_not_energise_anything(client):
    """Standing the controller down is not a reason for a relay to close."""
    body = client.post("/api/test-mode", json={"enabled": True}).get_json()
    assert set(body["requested_outputs"].values()) == {False}


def test_a_past_deadline_reads_as_off(client):
    """The backstop for a TTL that somehow did not fire."""
    client.redis.store["test_mode_until"] = str(_now() - 1)
    assert client.get("/api/test-mode").get_json()["active"] is False


def test_leaving_test_mode_also_clears_what_was_switched_on(client):
    """A later session must not begin by energising the leftovers of this one."""
    client.post("/api/test-mode", json={"enabled": True})
    client.post("/api/test-mode/outputs", json={"name": "ventilation", "state": True})

    client.post("/api/test-mode", json={"enabled": False})

    assert "test_mode_until" not in client.redis.store
    assert "test_mode_outputs" not in client.redis.store


def test_an_output_request_is_refused_outside_test_mode(client):
    """The loop would ignore it, and a request that silently does nothing is worse."""
    response = client.post("/api/test-mode/outputs", json={"name": "ventilation", "state": True})
    assert response.status_code == 409


def test_an_output_request_is_published_for_the_loop(client):
    client.post("/api/test-mode", json={"enabled": True})
    body = client.post(
        "/api/test-mode/outputs", json={"name": "humidity_up", "state": True}).get_json()

    assert body["requested_outputs"]["humidity_up"] is True
    assert body["requested_outputs"]["ventilation"] is False
    assert json.loads(client.redis.store["test_mode_outputs"])["humidity_up"] is True


def test_the_requested_states_cannot_outlive_the_mode(client):
    """Otherwise they would be waiting to apply the next time it is turned on."""
    client.post("/api/test-mode", json={"enabled": True, "duration_seconds": 300})
    client.post("/api/test-mode/outputs", json={"name": "ventilation", "state": True})

    assert client.redis.ttls["test_mode_outputs"] <= 300


def test_test_mode_refuses_to_last_longer_than_a_day(client):
    response = client.post("/api/test-mode", json={"enabled": True, "duration_seconds": 10 ** 9})
    assert response.status_code == 400
    assert "test_mode_until" not in client.redis.store


@pytest.mark.parametrize("body", [{}, {"enabled": "si"}, {"enabled": 1}])
def test_test_mode_needs_an_explicit_boolean(client, body):
    assert client.post("/api/test-mode", json=body).status_code == 400


def test_an_unknown_output_is_refused_by_name(client):
    client.post("/api/test-mode", json={"enabled": True})
    response = client.post("/api/test-mode/outputs", json={"name": "luz", "state": True})
    assert response.status_code == 404
