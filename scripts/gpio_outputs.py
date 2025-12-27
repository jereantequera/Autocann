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
    },
    {
        "name": "humidity_down",
        "label": "Humedad abajo (bajar humedad)",
        # NOTE: This matches current `fix-vpd.py` config. Change here when wiring is confirmed.
        "pin_bcm": 16,
        "redis_key": "humidity_control_down",
    },
    {
        "name": "ventilation",
        "label": "Ventilación (extracción)",
        "pin_bcm": 7,
        "redis_key": "ventilation_control",
    },
]


