"""
GPIO output configuration shared by the control loop and the web dashboard.

Keep this module free of Raspberry Pi specific imports (gpiozero, RPi.GPIO, …)
and of Flask, so both sides can import it on any machine.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from autocann.config import gpio_pins_from_env


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


def find_output(name: str) -> Optional[Dict[str, Any]]:
    for output in get_outputs():
        if output.get("name") == name:
            return output
    return None


def output_names() -> List[str]:
    return [output["name"] for output in get_outputs()]


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
