from __future__ import annotations

import re
from dataclasses import asdict

import pytest

from autocann.cli.vpd import Relays
from autocann.config import (
    RELAY_MODE_GPIO,
    RELAY_MODE_REMOTE,
    ControlTuning,
    GpioPins,
    RelayNodePins,
    relay_node_from_env,
)
from autocann.control.humidity import DEHUMIDIFY, HUMIDIFY, IDLE
from autocann.hardware.outputs import (
    PIN_SCOPE_LOCAL,
    PIN_SCOPE_REMOTE,
    get_outputs,
    pin_sources,
)
from autocann.paths import PROJECT_ROOT


class FakeDevice:
    """Stands in for gpiozero.OutputDevice and counts every switch."""

    def __init__(self):
        self.value = False
        self.switches = 0
        self.closed = False

    def on(self):
        self.value = True
        self.switches += 1

    def off(self):
        self.value = False
        self.switches += 1

    def close(self):
        self.closed = True


@pytest.fixture
def relays(monkeypatch):
    r = Relays()
    devices = {name: FakeDevice() for name in r._specs}
    r._devices = devices
    monkeypatch.setattr("autocann.cli.vpd.store_control_event", lambda *a, **k: True)
    monkeypatch.setattr("autocann.cli.vpd._set_redis", lambda *a, **k: None)
    r.devices = devices
    return r


def state(relays):
    return {name: device.value for name, device in relays.devices.items()}


def test_humidify_energises_only_the_humidifier(relays):
    relays.apply(HUMIDIFY)
    assert state(relays) == {"humidity_up": True, "humidity_down": False, "ventilation": False}


def test_dehumidify_energises_only_the_dehumidifier(relays):
    relays.apply(DEHUMIDIFY)
    assert state(relays) == {"humidity_up": False, "humidity_down": True, "ventilation": False}


def test_both_humidity_outputs_are_never_on_together(relays):
    for action in (HUMIDIFY, DEHUMIDIFY, IDLE, HUMIDIFY, DEHUMIDIFY):
        relays.apply(action)
        current = state(relays)
        assert not (current["humidity_up"] and current["humidity_down"])


def test_an_unchanged_action_does_not_touch_the_relay(relays):
    relays.apply(HUMIDIFY)
    switches = relays.devices["humidity_up"].switches
    for _ in range(20):
        relays.apply(HUMIDIFY)
    assert relays.devices["humidity_up"].switches == switches


def test_a_manual_override_does_not_chatter_against_automatic_control(relays):
    """
    Applying the automatic action and then the override on top toggled an
    overridden relay off and on again on every iteration.
    """
    overrides = {"ventilation": True}
    relays.apply(IDLE, overrides=overrides)
    switches = relays.devices["ventilation"].switches
    assert relays.devices["ventilation"].value is True

    for _ in range(20):                       # 20 loop iterations = one minute
        relays.apply(IDLE, overrides=overrides)
    assert relays.devices["ventilation"].switches == switches
    assert relays.devices["ventilation"].value is True


def test_an_override_wins_over_the_automatic_action(relays):
    relays.apply(HUMIDIFY, overrides={"humidity_up": False})
    assert relays.devices["humidity_up"].value is False

    relays.apply(IDLE, overrides={"humidity_down": True})
    assert relays.devices["humidity_down"].value is True


def test_an_unknown_override_name_is_ignored(relays):
    relays.apply(IDLE, overrides={"not_a_real_output": True})
    assert state(relays) == {"humidity_up": False, "humidity_down": False, "ventilation": False}


def test_all_off_ignores_overrides(relays):
    """The failsafe must win over a manual command."""
    relays.apply(IDLE, overrides={"ventilation": True, "humidity_up": True})
    relays.all_off()
    assert not any(state(relays).values())


def test_close_de_energises_before_releasing_the_pins(relays):
    relays.apply(HUMIDIFY)
    relays.close()
    assert relays.devices["humidity_up"].value is False
    assert all(device.closed for device in relays.devices.values())


def test_a_failing_relay_does_not_corrupt_the_tracked_state(relays):
    class Broken(FakeDevice):
        def on(self):
            raise OSError("GPIO busy")

    relays._devices["humidity_up"] = relays.devices["humidity_up"] = Broken()
    relays.apply(HUMIDIFY)
    # The switch failed, so it must not be recorded as on, or a later retry
    # would be skipped as "no change".
    assert relays._state["humidity_up"] is False


# ---------------------------------------------------------------------------
# Remote relay node
# ---------------------------------------------------------------------------


@pytest.fixture
def published(monkeypatch):
    """A relay set in remote mode, recording everything it publishes."""
    import json

    from autocann.config import RELAY_MODE_REMOTE

    writes = []
    monkeypatch.setattr("autocann.cli.vpd.store_control_event", lambda *a, **k: True)
    monkeypatch.setattr(
        "autocann.cli.vpd._set_redis",
        lambda key, value, **k: writes.append((key, json.loads(value)))
        if key == "desired_outputs" else None,
    )
    r = Relays(mode=RELAY_MODE_REMOTE)
    r.setup()
    writes.clear()        # drop the all_off() from setup
    r.writes = writes
    return r


def test_remote_mode_never_touches_gpio(monkeypatch):
    """setup() must not import or construct gpiozero devices off a Pi."""
    from autocann.config import RELAY_MODE_REMOTE

    monkeypatch.setattr("autocann.cli.vpd.store_control_event", lambda *a, **k: True)
    monkeypatch.setattr("autocann.cli.vpd._set_redis", lambda *a, **k: None)

    r = Relays(mode=RELAY_MODE_REMOTE)
    r.setup()
    r.apply(HUMIDIFY)

    assert all(device is None for device in r._devices.values())


def test_every_cycle_publishes_the_full_state_even_when_nothing_changed(published):
    """
    The poll is also the keepalive, so a cycle that changes nothing still has
    to be published. Silence is what tells the node the loop is gone.
    """
    published.apply(HUMIDIFY)
    published.apply(HUMIDIFY)
    published.apply(HUMIDIFY)

    assert len(published.writes) == 3
    for _, payload in published.writes:
        assert payload["outputs"] == {
            "humidity_up": True, "humidity_down": False, "ventilation": False,
        }
        assert payload["ts"] > 0


def test_a_relay_the_node_dropped_on_its_own_is_re_sent(published):
    """
    The node's watchdog can de-energise without telling the loop. The loop's
    cached state would then still say 'on' and set_output would short-circuit
    forever, so the output must come back from the published state, not from
    a change event.
    """
    published.apply(HUMIDIFY)
    published.writes.clear()

    # The node drops everything on its own; the loop is none the wiser and
    # keeps asking for the same thing.
    published.apply(HUMIDIFY)

    assert published.writes[-1][1]["outputs"]["humidity_up"] is True


def test_all_off_is_published_too(published):
    published.apply(DEHUMIDIFY)
    published.all_off()
    assert published.writes[-1][1]["outputs"] == {
        "humidity_up": False, "humidity_down": False, "ventilation": False,
    }


def test_the_published_state_carries_every_output(published):
    """A node must never have to guess what to do with an output."""
    published.apply(IDLE)
    outputs = published.writes[-1][1]["outputs"]
    assert set(outputs) == set(published._specs)


# ---------------------------------------------------------------------------
# Test mode: the relays follow a person, not a decision
# ---------------------------------------------------------------------------

TUNING = ControlTuning(min_on_seconds=60.0, min_off_seconds=180.0)


def test_manual_drive_puts_every_output_exactly_where_asked(relays):
    held = relays.apply_manual(
        {"humidity_up": True, "ventilation": True}, tuning=TUNING, now=1000.0)

    assert state(relays) == {
        "humidity_up": True, "humidity_down": False, "ventilation": True,
    }
    assert held == {}


def test_manual_drive_can_do_what_no_automatic_action_can(relays):
    """
    `apply` derives the humidity outputs from one action, so it can never ask
    for both. A bench test has to be able to: that is what testing wiring is.
    """
    relays.apply_manual(
        {"humidity_up": True, "humidity_down": True}, tuning=TUNING, now=1000.0)

    assert state(relays)["humidity_up"] and state(relays)["humidity_down"]


def test_manual_drive_writes_no_history(relays, monkeypatch):
    """Test mode stores nothing: a bench session is not history."""
    events = []
    monkeypatch.setattr("autocann.cli.vpd.store_control_event",
                        lambda *a, **k: events.append(a))

    relays.apply_manual({"ventilation": True}, tuning=TUNING, now=1000.0)
    relays.apply_manual({"ventilation": False}, tuning=TUNING, now=1000.0 + 999)

    assert events == []


def test_the_minimum_times_still_hold_for_a_person(relays):
    """
    The equipment does not care whether the command came from a person or from
    the controller.
    """
    relays.apply_manual({"ventilation": True}, tuning=TUNING, now=1000.0)
    held = relays.apply_manual({"ventilation": False}, tuning=TUNING, now=1010.0)

    assert state(relays)["ventilation"] is True, "no debería haberse apagado"
    assert held == {"ventilation": pytest.approx(50.0)}


def test_a_held_command_is_reported_not_dropped(relays):
    """A relay that does not move must say why, or it just looks broken."""
    relays.apply_manual({"humidity_up": True}, tuning=TUNING, now=1000.0)
    held = relays.apply_manual({"humidity_up": False}, tuning=TUNING, now=1030.0)

    assert "humidity_up" in held and held["humidity_up"] > 0


def test_the_command_goes_through_once_the_minimum_is_served(relays):
    relays.apply_manual({"ventilation": True}, tuning=TUNING, now=1000.0)
    assert relays.apply_manual({"ventilation": False}, tuning=TUNING, now=1061.0) == {}
    assert state(relays)["ventilation"] is False


def test_an_output_that_never_switched_owes_no_waiting_time(relays):
    """Otherwise the first command of a session would always be held back."""
    assert relays.hold_remaining("ventilation", TUNING, now=0.0) == 0.0
    assert relays.apply_manual({"ventilation": True}, tuning=TUNING, now=0.0) == {}


def test_asking_for_a_state_an_output_already_has_is_never_held(relays):
    """It is not a transition, so there is nothing to protect."""
    relays.apply_manual({"ventilation": True}, tuning=TUNING, now=1000.0)
    assert relays.apply_manual({"ventilation": True}, tuning=TUNING, now=1005.0) == {}


def test_an_output_that_never_switched_still_reports_that_it_is_off(monkeypatch):
    """
    `set_output` writes nothing when nothing changed, so without this an output
    that has never moved has no Redis key — and the dashboard draws a dash over
    a relay that is known to be off.
    """
    written = {}
    monkeypatch.setattr("autocann.cli.vpd._set_redis",
                        lambda key, value, **k: written.__setitem__(key, value))
    monkeypatch.setattr("autocann.cli.vpd.store_control_event", lambda *a, **k: True)

    Relays(mode=RELAY_MODE_REMOTE).setup()

    for spec in get_outputs():
        assert written.get(spec["redis_key"]) == "false", spec["name"]


def test_test_mode_keeps_the_shutdown_path_out_of_the_history(relays, monkeypatch):
    """
    The paths that de-energise without being asked — the shutdown handler, the
    loop's catch-all — are the ones a per-call-site flag would forget, and they
    run with exactly the relays a bench session had switched on.
    """
    events = []
    monkeypatch.setattr("autocann.cli.vpd.store_control_event",
                        lambda *a, **k: events.append(a))

    relays.log_events = False
    relays.apply_manual({"ventilation": True}, tuning=TUNING, now=1000.0)
    relays.all_off()          # as the shutdown handler calls it: logging on

    assert events == []
    assert state(relays)["ventilation"] is False


def test_leaving_test_mode_puts_the_history_back(relays, monkeypatch):
    events = []
    monkeypatch.setattr("autocann.cli.vpd.store_control_event",
                        lambda *a, **k: events.append(a))

    relays.log_events = False
    relays.apply_manual({"ventilation": True}, tuning=TUNING, now=1000.0)
    relays.log_events = True
    relays.apply(IDLE)

    assert events == [("ventilation", "off")]


# ---------------------------------------------------------------------------
# Where the pins come from
# ---------------------------------------------------------------------------


def test_the_node_pin_mapping_matches_the_firmware():
    """
    The node never reports its own wiring — it only polls — so the backend
    carries the mapping and `relay_node.ino` is the authority. A reflash that
    moves a pin, with this mapping left behind, would print a number on the
    dashboard that belongs to another relay: the exact defect the mapping was
    added to fix, back again with nothing to say so.
    """
    source = (PROJECT_ROOT / "firmware" / "relay_node" / "relay_node.ino").read_text()
    array = re.search(r"OUTPUTS\[\]\s*=\s*\{(.*?)\};", source, re.S)
    assert array, "no se encontró el arreglo OUTPUTS en firmware/relay_node"

    wired = {name: int(pin) for name, pin in re.findall(r'"(\w+)",\s*(\d+)', array.group(1))}
    assert wired == asdict(RelayNodePins())


def test_the_pin_shown_is_the_one_in_the_circuit(monkeypatch):
    """
    In remote mode the Raspberry's header is not in the circuit at all, and one
    of its numbers (BCM 25, ventilation) is also a real pin on the node, where
    it drives the humidifier.
    """
    monkeypatch.setenv("AUTOCANN_RELAY_MODE", RELAY_MODE_REMOTE)
    remote = pin_sources()
    assert {name: source["number"] for name, source in remote.items()} == asdict(RelayNodePins())
    assert {source["scope"] for source in remote.values()} == {PIN_SCOPE_REMOTE}
    assert remote["ventilation"]["number"] != GpioPins().ventilation

    monkeypatch.setenv("AUTOCANN_RELAY_MODE", RELAY_MODE_GPIO)
    local = pin_sources()
    assert {name: source["number"] for name, source in local.items()} == asdict(GpioPins())
    assert {source["scope"] for source in local.values()} == {PIN_SCOPE_LOCAL}
    # Nothing to walk up to: the pins are on the board running this code.
    assert {source["node"] for source in local.values()} == {None}


def test_the_node_pins_are_overridable_without_touching_code(monkeypatch):
    monkeypatch.setenv("AUTOCANN_RELAY_MODE", RELAY_MODE_REMOTE)
    monkeypatch.setenv("AUTOCANN_RELAY_NODE_PIN_VENTILATION", "32")
    monkeypatch.setenv("AUTOCANN_RELAY_NODE_NAME", "ESP32 relés (192.168.100.77)")

    sources = pin_sources()
    assert sources["ventilation"]["number"] == 32
    assert sources["ventilation"]["node"] == "ESP32 relés (192.168.100.77)"


def test_two_outputs_on_one_node_pin_are_rejected(monkeypatch):
    """They would switch together, same as on the Raspberry's header."""
    monkeypatch.setenv("AUTOCANN_RELAY_NODE_PIN_VENTILATION", str(RelayNodePins().humidity_up))
    with pytest.raises(ValueError, match="share a pin"):
        relay_node_from_env()


def test_a_loop_told_its_mode_does_not_go_back_to_the_environment(monkeypatch):
    """
    The loop is handed its mode; the dashboard reads it from the environment.
    If the two could disagree, the loop's startup log and the panel would print
    different pins for the same relay — and both would look authoritative.
    """
    monkeypatch.delenv("AUTOCANN_RELAY_MODE", raising=False)
    assert pin_sources(RELAY_MODE_REMOTE)["humidity_up"]["number"] == RelayNodePins().humidity_up
