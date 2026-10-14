from __future__ import annotations

import json

import pytest

from autocann.config import GpioPins, RelayNode, RelayNodePins
from autocann.hardware.outputs import manual_override_key, output_names, parse_bool_flag

# FakeRedis y la fixture `client` viven en conftest.py: las usa también
# tests/test_diagnostics.py, y dos fakes de Redis distintos se van separando
# justo en lo que el otro módulo da por sentado.


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


def test_output_status_names_the_board_that_actually_switches_each_relay(client, monkeypatch):
    """
    The panel showed `pin_bcm` as "BCM n" in both modes. With the relays on the
    node, those were pins of a board that is not in the circuit — and one of
    them, BCM 25, is a real pin on the node, where it drives the humidifier.
    """
    monkeypatch.setenv("AUTOCANN_RELAY_MODE", "remote")

    outputs = {o["name"]: o for o in client.get("/api/output-status").get_json()["outputs"]}
    assert outputs["ventilation"]["pin"] == {
        "scope": "remote",
        "node": RelayNode().name,
        "number": RelayNodePins().ventilation,
    }
    # The Raspberry's number does not travel any more: it switches nothing.
    assert "pin_bcm" not in outputs["ventilation"]


def test_output_status_still_reports_the_raspberry_pin_when_it_is_the_one_switching(
    client, monkeypatch
):
    monkeypatch.setenv("AUTOCANN_RELAY_MODE", "gpio")

    outputs = {o["name"]: o for o in client.get("/api/output-status").get_json()["outputs"]}
    assert outputs["ventilation"]["pin"] == {
        "scope": "local",
        "node": None,
        "number": GpioPins().ventilation,
    }


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
    """
    Rows as the control loop writes them.

    The `datetime` column carries the real local stamp rather than an empty
    string: the weekly report groups by `strftime('%H', datetime)`, so rows
    without one give it a NULL hour and it never reaches the code under test.
    """
    from datetime import datetime as dt

    from autocann.time import ARGENTINA_TZ

    conn = db._open()
    for ts in timestamps:
        conn.execute(
            "INSERT INTO sensor_data (grow_id, timestamp, datetime, temperature, humidity,"
            " vpd, outside_temperature, outside_humidity) VALUES (?, ?, ?, 24, 50, 1.2, 15, 70)",
            (grow_id, ts,
             dt.fromtimestamp(ts, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S")),
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


def test_the_pause_is_off_until_it_is_asked_for(client):
    body = client.get("/api/test-mode").get_json()
    assert body["active"] is False
    assert body["since"] is None


def test_the_pause_lasts_until_somebody_ends_it(client):
    """
    It used to expire after eight hours. A pause that resumes by itself
    energises relays at a moment nobody chose - and if it was taken to rewire
    something, while somebody's hands are in there.
    """
    body = client.post("/api/test-mode", json={"enabled": True}).get_json()

    assert body["active"] is True
    assert body["since"] <= _now()
    assert body["elapsed_seconds"] >= 0
    # No expiry on either key: the only thing that ends this is a person.
    assert client.redis.ttls["test_mode_since"] is None
    assert client.redis.ttls["test_mode_outputs"] is None


def test_the_pause_survives_losing_redis(client):
    """
    Redis is memory. Restarting it, or the Raspberry, used to hand the tent
    back to the controller with nothing to say so.
    """
    client.post("/api/test-mode", json={"enabled": True})
    client.redis.store.clear()

    body = client.get("/api/test-mode").get_json()
    assert body["active"] is True
    # And the mirror the control loop reads is put back, because the loop
    # never looks at the database on the fast path.
    assert client.redis.store["test_mode_since"]


def test_pausing_does_not_energise_anything(client):
    """Standing the controller down is not a reason for a relay to close."""
    body = client.post("/api/test-mode", json={"enabled": True}).get_json()
    assert set(body["requested_outputs"].values()) == {False}


def test_resuming_also_clears_what_was_switched_on(client):
    """A later pause must not begin by energising the leftovers of this one."""
    client.post("/api/test-mode", json={"enabled": True})
    client.post("/api/test-mode/outputs", json={"name": "ventilation", "state": True})

    client.post("/api/test-mode", json={"enabled": False})

    assert "test_mode_since" not in client.redis.store
    assert "test_mode_outputs" not in client.redis.store
    # Cleared in the database too, or the next read would restore the mirror
    # and put the tent straight back into a pause nobody asked for.
    assert client.get("/api/test-mode").get_json()["active"] is False


def test_an_output_request_is_refused_while_the_loop_is_in_control(client):
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


@pytest.mark.parametrize("body", [{}, {"enabled": "si"}, {"enabled": 1}])
def test_the_pause_needs_an_explicit_boolean(client, body):
    assert client.post("/api/test-mode", json=body).status_code == 400


def test_an_unknown_output_is_refused_by_name(client):
    client.post("/api/test-mode", json={"enabled": True})
    response = client.post("/api/test-mode/outputs", json={"name": "luz", "state": True})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------

NIGHT = {"enabled": True, "start": "23:00", "end": "07:00", "max_humidity": 85}


def test_quiet_hours_start_out_unset(client):
    body = client.get("/api/quiet-hours").get_json()
    assert body["windows"] == {}
    # Served rather than assumed: a window on an output nothing drives
    # automatically would be a setting that is quietly inert.
    assert body["automatic_outputs"] == ["humidity_down", "humidity_up"]


def test_a_window_survives_the_round_trip(client):
    client.put("/api/quiet-hours", json={"windows": {"humidity_down": NIGHT}})

    stored = client.get("/api/quiet-hours").get_json()["windows"]["humidity_down"]
    assert stored == {"enabled": True, "start": "23:00", "end": "07:00", "max_humidity": 85.0}


def test_a_hard_block_is_stored_as_one(client):
    """No escape is a real choice, not a missing field."""
    client.put("/api/quiet-hours", json={
        "windows": {"humidity_down": {"enabled": True, "start": "23:00", "end": "07:00"}}})

    assert client.get("/api/quiet-hours").get_json()["windows"]["humidity_down"][
        "max_humidity"] is None


def test_an_enabled_window_needs_real_times(client):
    response = client.put("/api/quiet-hours", json={
        "windows": {"humidity_down": {"enabled": True, "start": "la noche", "end": "07:00"}}})
    assert response.status_code == 400


def test_a_draft_nobody_switched_on_is_not_an_error(client):
    """Half-filled configuration is a draft, not a mistake."""
    response = client.put("/api/quiet-hours", json={
        "windows": {"humidity_down": {"enabled": False, "start": "", "end": ""}}})
    assert response.status_code == 200


def test_a_zero_width_window_is_refused(client):
    response = client.put("/api/quiet-hours", json={
        "windows": {"humidity_down": {"enabled": True, "start": "03:00", "end": "03:00"}}})
    assert response.status_code == 400
    assert "cero" in response.get_json()["error"]


@pytest.mark.parametrize("value", [-1, 101, "mucho"])
def test_the_escape_threshold_has_to_be_a_humidity(client, value):
    response = client.put("/api/quiet-hours", json={
        "windows": {"humidity_down": {**NIGHT, "max_humidity": value}}})
    assert response.status_code == 400


def test_a_window_on_an_unknown_output_is_refused(client):
    response = client.put("/api/quiet-hours", json={"windows": {"luz": NIGHT}})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Grow scope
# ---------------------------------------------------------------------------


def _closed_and_active(client, temp_db, now):
    """A closed grow with cold samples, and an active one with warm samples."""
    closed = temp_db.create_grow("Prueba", "flowering")
    _seed(temp_db, closed, [now - 7200 - i * 300 for i in range(6)])
    temp_db.end_grow(closed)

    active = temp_db.create_grow("Real", "early_veg")
    _seed(temp_db, active, [now - i * 300 for i in range(6)])
    return closed, active


def test_the_history_endpoint_answers_about_the_active_grow(client, temp_db):
    """
    The default, defended: everything else on screen — the stage, the day
    counter, the target band — is about the active grow, so a 30-day average
    that includes the previous crop is a number labelled with the wrong context.
    """
    import time

    now = int(time.time())
    _closed, active = _closed_and_active(client, temp_db, now)

    payload = client.get(
        f"/api/sensor-history?start={now - 90 * 86400}&end={now}").get_json()
    assert payload["grow_id"] == active
    assert payload["grow_scope"] == "active"
    assert {p["grow_id"] for p in payload["data"] if not p.get("gap")} == {active}


def test_the_history_endpoint_can_be_pointed_at_one_grow(client, temp_db):
    import time

    now = int(time.time())
    closed, _active = _closed_and_active(client, temp_db, now)

    payload = client.get(
        f"/api/sensor-history?start={now - 90 * 86400}&end={now}"
        f"&grow_id={closed}").get_json()
    assert payload["grow_scope"] == "grow"
    assert {p["grow_id"] for p in payload["data"] if not p.get("gap")} == {closed}


def test_a_window_over_two_grows_does_not_draw_them_as_one_series(client, temp_db):
    """
    The changeover is the most misleading shape this chart can take: the two
    runs are five minutes of real time apart, so no outage threshold catches
    it, and the line goes straight from the end of one crop to the start of
    the next.
    """
    import time

    now = int(time.time())
    closed, active = _closed_and_active(client, temp_db, now)

    payload = client.get(
        f"/api/sensor-history?start={now - 90 * 86400}&end={now}"
        f"&grow_id=all").get_json()
    assert payload["grow_scope"] == "all"
    assert {p["grow_id"] for p in payload["data"] if not p.get("gap")} == {closed, active}

    changes = [p for p in payload["data"] if p.get("series_change") == "grow_id"]
    assert len(changes) == 1


def test_the_aggregated_endpoint_breaks_at_the_grow_boundary_too(client, temp_db):
    import time

    now = int(time.time())
    _closed_and_active(client, temp_db, now)

    payload = client.get("/api/history/aggregated?days=7&grow_id=all").get_json()
    assert payload["grow_scope"] == "all"
    assert any(p.get("series_change") == "grow_id" for p in payload["data"])


def test_every_scoped_endpoint_says_which_grow_it_answered_about(client, temp_db):
    """
    The scope is resolved server-side, so a chart that does not ask has no way
    to label itself — and one whose meaning changes the moment somebody
    activates another grow, silently, is worse than one with no label.
    """
    import time

    now = int(time.time())
    _closed, active = _closed_and_active(client, temp_db, now)

    for url in ("/api/period-summary?days=7",
                "/api/history/aggregated?days=7",
                "/api/vpd-score?days=7",
                "/api/weekly-report",
                f"/api/sensor-history?start={now - 3600}&end={now}"):
        payload = client.get(url).get_json()
        assert payload.get("grow_id") == active, url
        assert payload.get("grow_scope") == "active", url


def test_the_vpd_score_refuses_every_grow_at_once_through_the_api(client, temp_db):
    import time

    now = int(time.time())
    _closed_and_active(client, temp_db, now)

    assert "error" in client.get("/api/vpd-score?days=7&grow_id=all").get_json()
    assert "error" in client.get("/api/weekly-report?grow_id=all").get_json()


@pytest.mark.parametrize("value", ["todos", "1.5", "' OR 1=1", ""])
def test_a_grow_scope_that_is_not_one_is_refused_rather_than_ignored(client, value):
    """
    Falling back to a different scope than the caller asked for is how a chart
    ends up quietly answering a question nobody put.
    """
    response = client.get(f"/api/sensor-history?period=24h&grow_id={value}")
    if value == "":
        assert response.status_code == 200      # absent means the active grow
    else:
        assert response.status_code == 400
