"""
GPIO output configuration shared by backend/dashboard and control scripts.

Keep this module free of Raspberry Pi specific imports (gpiozero, RPi.GPIO, etc)
so it can be safely imported on non-RPi machines.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from autocann.config import gpio_pins_from_env


def get_outputs() -> List[Dict[str, Any]]:
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


# Backwards compatible constant name.
OUTPUTS: List[Dict[str, Any]] = get_outputs()


def find_output(name: str) -> Optional[Dict[str, Any]]:
    for o in OUTPUTS:
        if o.get("name") == name:
            return o
    return None

