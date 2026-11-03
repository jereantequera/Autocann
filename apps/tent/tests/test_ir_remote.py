"""
The humidifier's virtual remote: pressing a button on the dashboard, and the
relay node picking it up on its poll of /api/outputs/desired.

What matters most is that a press goes out at most once. POWER is a toggle,
so a press delivered twice undoes itself and leaves the humidifier off.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from autocann.hardware.ir_remote import IR_BUTTONS, IR_COMMAND_TTL_SECONDS, IR_PENDING_KEY, find_button
from autocann.time import ARGENTINA_TZ

FIRMWARE = Path(__file__).parent.parent / "firmware" / "relay_node"


def _power(client, humidifier_on=True, age_seconds=0):
    now = int(datetime.now(ARGENTINA_TZ).timestamp())
    client.redis.store["desired_outputs"] = json.dumps({
        "outputs": {"humidity_up": humidifier_on, "humidity_down": False, "ventilation": False},
        "ts": now - age_seconds,
    })


def _press(client, button="power"):
    return client.post("/api/ir-remote/press", json={"button": button})


def _poll(client, ack=None):
    url = "/api/outputs/desired" + (f"?ir_ack={ack}" if ack is not None else "")
    return client.get(url).get_json()


def test_the_remote_lists_every_captured_button(client):
    body = client.get("/api/ir-remote").get_json()
    assert [b["key"] for b in body["buttons"]] == [b.key for b in IR_BUTTONS]
    assert body["pending"] is None and body["last"] is None


def test_a_press_reaches_the_node_and_the_ack_clears_it(client):
    _power(client)
    command = _press(client).get_json()["command"]

    poll = _poll(client)
    assert poll["ir"] == {"id": command["id"], "code": "0x01FE48B7"}

    # The node sent it and repeats the id on the next poll.
    assert _poll(client, ack=command["id"])["ir"] is None
    status = client.get("/api/ir-remote").get_json()
    assert status["pending"] is None
    assert status["last"]["id"] == command["id"]
    assert status["last"]["button"] == "power"


def test_an_old_ack_does_not_clear_a_new_press(client):
    _power(client)
    first = _press(client).get_json()["command"]["id"]
    _poll(client, ack=first)
    second = _press(client).get_json()["command"]["id"]

    assert second != first
    assert _poll(client, ack=first)["ir"]["id"] == second


def test_a_second_press_while_the_first_is_in_flight_is_refused(client):
    """Stacking them would send POWER twice: the humidifier would end up off."""
    _power(client)
    assert _press(client).status_code == 200

    response = _press(client)
    assert response.status_code == 409
    assert response.get_json()["code"] == "ir_busy"


def test_a_press_expires_instead_of_firing_late(client):
    _power(client)
    _press(client)
    assert client.redis.ttls[IR_PENDING_KEY] == IR_COMMAND_TTL_SECONDS


def test_a_press_with_the_humidifier_unpowered_is_refused(client):
    _power(client, humidifier_on=False)
    response = _press(client)
    assert response.status_code == 409
    assert response.get_json()["code"] == "humidifier_off"


def test_a_stale_desired_state_counts_as_unpowered(client):
    """The node is served everything off then, so the humidifier has no power."""
    _power(client, age_seconds=600)
    assert _press(client).get_json()["code"] == "humidifier_off"


def test_the_node_gets_no_press_in_failsafe(client):
    _power(client)
    _press(client)
    _power(client, age_seconds=600)
    body = _poll(client)
    assert body["failsafe"] is True and body["ir"] is None


def test_unknown_and_missing_buttons_are_refused(client):
    _power(client)
    assert _press(client, "turbo").get_json()["code"] == "unknown_button"
    assert client.post("/api/ir-remote/press", json={}).get_json()["code"] == "missing_field"


def test_the_node_poll_does_not_need_a_press(client):
    _power(client)
    assert _poll(client, ack="garbage")["ir"] is None


def test_power_matches_the_code_the_firmware_sends_on_power_up():
    source = (FIRMWARE / "relay_node.ino").read_text()
    code = re.search(r"IR_POWER_CODE\s*=\s*(0x[0-9A-Fa-f]+)", source)
    assert code and int(code.group(1), 16) == find_button("power").code


def test_every_button_matches_the_capture_notes():
    notes = (FIRMWARE / "CONTROL_REMOTO.md").read_text()
    captured = {int(c, 16) for c in re.findall(r"`(0x01FE[0-9A-F]{4})`", notes)}
    assert {b.code for b in IR_BUTTONS} == captured
