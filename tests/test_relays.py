from __future__ import annotations

import pytest

from autocann.cli.vpd import Relays
from autocann.control.humidity import DEHUMIDIFY, HUMIDIFY, IDLE


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
