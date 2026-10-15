"""
GPIO output configuration shared by the control loop and the web dashboard.

Keep this module free of Raspberry Pi specific imports (gpiozero, RPi.GPIO, …)
and of Flask, so both sides can import it on any machine.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Optional

from autocann.config import (
    RELAY_MODE_REMOTE,
    gpio_pins_from_env,
    relay_mode_from_env,
    relay_node_from_env,
)

#: Which board a pin number belongs to. `local` is the Raspberry's own header
#: in BCM numbering; `remote` is the node that polls for the desired state, and
#: then the number is that node's GPIO.
PIN_SCOPE_LOCAL = "local"
PIN_SCOPE_REMOTE = "remote"

#: The outputs that exist, in the order the dashboard shows them.
#:
#: Kept apart from `get_outputs()` because the names are fixed while the pins
#: are not: `get_outputs()` has to resolve the Raspberry's header to answer at
#: all, and in remote mode that header is not in the circuit and may be left
#: with two outputs on one pin without anything being wrong. Asking which
#: outputs exist should not raise over a board nothing is wired to.
#: `tests/test_relays.py` fails if this list and `get_outputs()` drift apart.
OUTPUT_NAMES = ("humidity_up", "humidity_down", "ventilation")


def get_outputs() -> List[Dict[str, Any]]:
    """
    The relay outputs, read fresh so AUTOCANN_PIN_* env overrides always apply.
    """
    pins = gpio_pins_from_env()
    return [
        {
            "name": "humidity_up",
            "label": "Humedad arriba (humidificador)",
            "pin_bcm": pins.humidity_up,
            "redis_key": "humidity_control_up",
            # Relay modules are typically active-low (relay ON when pin is LOW)
            "active_high": False,
        },
        {
            "name": "humidity_down",
            "label": "Humedad abajo (bajar humedad)",
            "pin_bcm": pins.humidity_down,
            "redis_key": "humidity_control_down",
            "active_high": False,
        },
        {
            "name": "ventilation",
            "label": "Ventilación (extracción)",
            "pin_bcm": pins.ventilation,
            "redis_key": "ventilation_control",
            "active_high": False,
        },
    ]


def pin_sources(mode: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """
    Where each output is really switched: `{name: {scope, node, number}}`.

    The dashboard printed `pin_bcm` as "BCM n" whatever the mode. With
    AUTOCANN_RELAY_MODE=remote the Raspberry switches nothing, so those were
    three pins of a board that is not in the circuit — and 25, the number it
    printed for `ventilation`, is also a real pin on the node, where it drives
    `humidity_up`. A label like that is worse than no label: it sends you to the
    right header to measure the wrong relay.

    Resolved from the mode in force, so the number on screen is always the one
    that can actually be measured. `mode` defaults to the environment's, and is
    passed in by a caller that was handed its mode rather than reading it — the
    control loop is — so the two cannot answer differently.
    """
    if (relay_mode_from_env() if mode is None else mode) == RELAY_MODE_REMOTE:
        node = relay_node_from_env()
        pins = asdict(node.pins)
        return {
            name: {"scope": PIN_SCOPE_REMOTE, "node": node.name, "number": pins.get(name)}
            for name in output_names()
        }

    pins = asdict(gpio_pins_from_env())
    return {
        name: {"scope": PIN_SCOPE_LOCAL, "node": None, "number": pins.get(name)}
        for name in output_names()
    }


def find_output(name: str) -> Optional[Dict[str, Any]]:
    for output in get_outputs():
        if output.get("name") == name:
            return output
    return None


def output_names() -> List[str]:
    return list(OUTPUT_NAMES)


def manual_override_key(name: str) -> str:
    """
    Redis key carrying a manual override for `name`.

    The web API writes it with a TTL and the control loop reads it. Only the
    control loop touches the GPIO: a pin can have one owner, and a pin released
    by `close()` reverts to its default state, dropping the relay.
    """
    return f"manual_override:{name}"


def parse_bool_flag(raw: Any) -> Optional[bool]:
    """Parse a Redis flag into a bool, or None when absent or unrecognised."""
    if raw is None:
        return None
    try:
        value = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    except Exception:
        return None
    value = value.strip().lower()
    if value in ("true", "1", "on", "yes"):
        return True
    if value in ("false", "0", "off", "no"):
        return False
    return None


def __getattr__(name: str):
    # `OUTPUTS` used to be a module-level snapshot taken at import time, which
    # froze the pin mapping before any env override could be read.
    if name == "OUTPUTS":
        return get_outputs()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
