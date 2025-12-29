"""
GPIO output configuration shared by backend/dashboard and control scripts.

Keep this module free of Raspberry Pi specific imports (gpiozero, RPi.GPIO, etc)
so it can be safely imported in the Flask backend even on non-RPi machines.
"""

from __future__ import annotations

from typing import Any, Dict, List

OUTPUTS: List[Dict[str, Any]] = [
    {
        "name": "humidity_up",
        "label": "Humedad arriba (humidificador)",
        "pin_bcm": 25,
        "redis_key": "humidity_control_up",
        # Relay modules are typically active-low (relay ON when pin is LOW)
        "active_high": False,
    },
    {
        "name": "humidity_down",
        "label": "Humedad abajo (bajar humedad)",
        "pin_bcm": 16,
        "redis_key": "humidity_control_down",
        "active_high": False,
    },
    {
        "name": "ventilation",
        "label": "Ventilación (extracción)",
        "pin_bcm": 7,
        "redis_key": "ventilation_control",
        "active_high": False,
    },
]


