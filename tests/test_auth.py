"""
The PIN, and the three endpoints it must never stand in front of.

The one that matters most is `GET /api/outputs/desired`. The relay node polls
it every two seconds and de-energises the tent after sixty seconds without a
successful poll, so a 401 there opens every relay a minute later.
"""

from __future__ import annotations

import pytest

from autocann.web.auth import EXEMPT, AttemptLimiter, is_exempt, pin_from_env


@pytest.fixture
def locked(monkeypatch, temp_db):
    """A client whose backend has a PIN configured."""
    import autocann.web.app as web
    from tests.conftest import FakeRedis

    fake = FakeRedis()
    monkeypatch.setattr(web.redis, "Redis", lambda **kwargs: fake)
    monkeypatch.setenv("AUTOCANN_PIN", "4821")
    app = web.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    client.redis = fake
    return client


# ---------------------------------------------------------------------------
# Without the variable, nothing changes
# ---------------------------------------------------------------------------


def test_without_the_variable_nothing_asks_for_anything(client, monkeypatch):
    """
    A deploy that forgets AUTOCANN_PIN must not lock the grower out of their
    own dashboard. Not being able to see the tent is worse than being readable
    from the LAN it is already on.
    """
    assert client.get("/").status_code == 200
    assert client.get("/api/output-status").status_code == 200
    assert client.post("/api/output-control",
                       json={"name": "ventilation", "state": True}).status_code == 200


def test_an_empty_variable_reads_as_no_pin(monkeypatch):
    """`AUTOCANN_PIN=` in a unit file is turning it off, not asking for a lock
    that opens to an empty string."""
    monkeypatch.setenv("AUTOCANN_PIN", "   ")
    assert pin_from_env() is None
    monkeypatch.setenv("AUTOCANN_PIN", "4821")
    assert pin_from_env() == "4821"


def test_the_status_endpoint_says_there_is_no_pin(client):
    payload = client.get("/api/auth").get_json()
    assert payload == {"pin_required": False, "authenticated": True}


# ---------------------------------------------------------------------------
# The node endpoints
# ---------------------------------------------------------------------------


def test_the_relay_node_is_never_asked_for_a_pin(locked):
    """
    60 s without a successful poll and the node drops every relay in the tent.
    A 401 here is indistinguishable from a dead Raspberry.
    """
    response = locked.get("/api/outputs/desired")
    assert response.status_code == 200
    assert "outputs" in response.get_json()


def test_the_sensor_node_is_never_asked_for_a_pin(locked):
    """
    Quieter than the relay node but not harmless: no readings means the loop
    calls the sensor stale after 60 s and goes to failsafe, which also drops
    the relays.
    """
    assert locked.post("/api/sensor/indoor",
                       json={"temperature": 24.0, "humidity": 60.0,
                             "sensor_id": "a"}).status_code == 200
    assert locked.post("/api/node/log",
                       json={"node": "sensor", "lines": ["arranque"]}).status_code == 200


def test_the_exempt_list_names_routes_that_exist(locked):
    """
    A renamed route silently loses its exemption, and the tent finds out sixty
    seconds later.
    """
    rules = {(method, str(rule))
             for rule in locked.application.url_map.iter_rules()
             for method in rule.methods}
    for method, path in EXEMPT:
        assert (method, path) in rules, f"{method} {path}"


def test_nothing_else_is_exempt(locked):
    """
    The exemption is for the two nodes, which cannot grow a cookie jar without
    somebody standing in the tent with a USB cable. Everything else is the half
    of the API that moves relays.
    """
    assert is_exempt("GET", "/api/output-status") is False
    assert is_exempt("POST", "/api/output-control") is False
    assert is_exempt("POST", "/api/test-mode") is False
    # The method matters: the sensor endpoint is exempt as a writer, not as a
    # reader the dashboard also uses.
    assert is_exempt("POST", "/api/sensor/indoor") is True
    assert is_exempt("GET", "/api/sensor/indoor") is False


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method, path, body", [
    ("get", "/api/output-status", None),
    ("get", "/api/current-data", None),
    ("get", "/api/sensor-history", None),
    ("post", "/api/output-control", {"name": "ventilation", "state": True}),
    ("post", "/api/test-mode", {"enabled": True}),
    ("post", "/api/grows", {"name": "x"}),
])
def test_the_api_that_moves_hardware_needs_the_pin(locked, method, path, body):
    call = getattr(locked, method)
    response = call(path, json=body) if body is not None else call(path)
    assert response.status_code == 401
    assert response.get_json()["auth_required"] is True


def test_the_dashboard_itself_answers_with_the_login_page(locked):
    response = locked.get("/")
    assert response.status_code == 401
    assert b"login-form" in response.data


def test_the_login_page_can_still_load_its_stylesheet(locked):
    """A gate in front of /static leaves the login screen unstyled."""
    assert locked.get("/static/css/app.css").status_code == 200


def test_the_right_pin_opens_everything_and_keeps_it_open(locked):
    assert locked.post("/api/login", json={"pin": "4821"}).status_code == 200
    assert locked.get("/").status_code == 200
    assert locked.get("/api/output-status").status_code == 200
    # The cookie carries across requests; the dashboard is used from a phone and
    # re-typing a PIN per request is how it ends up written next to the door.
    assert locked.get("/api/auth").get_json() == {
        "pin_required": True, "authenticated": True}


def test_the_wrong_pin_opens_nothing(locked):
    assert locked.post("/api/login", json={"pin": "0000"}).status_code == 401
    assert locked.get("/api/output-status").status_code == 401


@pytest.mark.parametrize("payload", [{}, {"pin": None}, {"pin": 4821}, {"pin": ""}])
def test_a_login_without_a_pin_string_is_refused(locked, payload):
    assert locked.post("/api/login", json=payload).status_code == 401


def test_logging_out_closes_the_session(locked):
    locked.post("/api/login", json={"pin": "4821"})
    assert locked.post("/api/logout").status_code == 200
    assert locked.get("/api/output-status").status_code == 401


def test_changing_the_pin_ends_the_sessions_opened_with_the_old_one(monkeypatch, temp_db):
    """
    The session stores a fingerprint of the PIN that was accepted, not a flag,
    so rotating it actually rotates something.
    """
    import autocann.web.app as web
    from tests.conftest import FakeRedis

    monkeypatch.setattr(web.redis, "Redis", lambda **kwargs: FakeRedis())
    monkeypatch.setenv("AUTOCANN_PIN", "4821")
    first = web.create_app().test_client()
    first.post("/api/login", json={"pin": "4821"})
    cookie = first.get_cookie("session")
    assert cookie is not None

    monkeypatch.setenv("AUTOCANN_PIN", "9999")
    second = web.create_app().test_client()
    second.set_cookie("session", cookie.value)
    assert second.get("/api/output-status").status_code == 401


# ---------------------------------------------------------------------------
# Guessing
# ---------------------------------------------------------------------------


def test_repeated_wrong_pins_have_to_wait():
    """
    Four digits is ten thousand guesses, which a script on the same LAN gets
    through in seconds.
    """
    limiter = AttemptLimiter(max_attempts=3, lockout_seconds=60)
    assert limiter.retry_after("10.0.0.5") == 0

    for _ in range(3):
        limiter.record_failure("10.0.0.5")
    assert limiter.retry_after("10.0.0.5") > 0
    # One address waiting does not keep anybody else out.
    assert limiter.retry_after("10.0.0.9") == 0


def test_the_wait_is_lifted_once_it_has_passed(monkeypatch):
    limiter = AttemptLimiter(max_attempts=2, lockout_seconds=60)
    for _ in range(2):
        limiter.record_failure("10.0.0.5")
    assert limiter.retry_after("10.0.0.5") > 0

    monkeypatch.setattr(limiter, "_now", lambda: limiter._failures["10.0.0.5"][1] + 61)
    assert limiter.retry_after("10.0.0.5") == 0


def test_a_correct_pin_clears_the_counter():
    limiter = AttemptLimiter(max_attempts=3, lockout_seconds=60)
    limiter.record_failure("10.0.0.5")
    limiter.record_failure("10.0.0.5")
    limiter.reset("10.0.0.5")
    limiter.record_failure("10.0.0.5")
    assert limiter.retry_after("10.0.0.5") == 0


def test_the_lockout_answers_429_rather_than_a_plain_refusal(locked):
    for _ in range(11):
        locked.post("/api/login", json={"pin": "0000"})
    response = locked.post("/api/login", json={"pin": "0000"})
    assert response.status_code == 429
    assert response.get_json()["retry_after_seconds"] > 0


def test_the_nodes_keep_working_while_an_address_is_locked_out(locked):
    """The failsafe must not be reachable by typing a wrong PIN eleven times."""
    for _ in range(12):
        locked.post("/api/login", json={"pin": "0000"})
    assert locked.get("/api/outputs/desired").status_code == 200


# ---------------------------------------------------------------------------
# Where somebody will look
# ---------------------------------------------------------------------------


def test_the_variable_is_documented_where_the_environment_is_set():
    """
    `scripts/start_services.sh` is the file somebody opens to configure an
    installation. A setting that only exists in a test is a setting nobody
    turns on.
    """
    from autocann.paths import PROJECT_ROOT

    script = (PROJECT_ROOT / "scripts" / "start_services.sh").read_text()
    assert "AUTOCANN_PIN" in script
    assert "60 s" in script, "el script tiene que decir qué pasa a los 60 s"

    install = (PROJECT_ROOT / "INSTALL.md").read_text()
    assert "AUTOCANN_PIN" in install
    assert "/api/outputs/desired" in install


def test_the_exemption_carries_its_reason_next_to_itself():
    """
    Somebody will read this list, see three unauthenticated endpoints and want
    to tidy them up. The consequence has to be written where they are looking,
    not in a changelog entry from months ago.
    """
    from pathlib import Path

    import autocann.web.auth as auth

    source = Path(auth.__file__).read_text()
    head = source[: source.index("_LOGIN_PATHS")]
    assert "60 seconds" in head
    assert "de-energises" in head
    assert "flashed by hand" in head
