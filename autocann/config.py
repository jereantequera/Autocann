from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RedisConfig:
    host: str = "localhost"
    port: int = 6379
    db: int = 0


def redis_config_from_env() -> RedisConfig:
    return RedisConfig(
        host=os.getenv("AUTOCANN_REDIS_HOST", "localhost"),
        port=int(os.getenv("AUTOCANN_REDIS_PORT", "6379")),
        db=int(os.getenv("AUTOCANN_REDIS_DB", "0")),
    )


@dataclass(frozen=True)
class GpioPins:
    # Defaults aligned with README.md
    humidity_up: int = 7
    humidity_down: int = 16
    ventilation: int = 25


def gpio_pins_from_env() -> GpioPins:
    """
    Allow overriding pin mapping without changing code.
    Useful when wiring differs between installations.
    """

    def _get_int(name: str, default: int) -> int:
        val = os.getenv(name)
        return default if val is None or val == "" else int(val)

    return GpioPins(
        humidity_up=_get_int("AUTOCANN_PIN_HUMIDITY_UP", 7),
        humidity_down=_get_int("AUTOCANN_PIN_HUMIDITY_DOWN", 16),
        ventilation=_get_int("AUTOCANN_PIN_VENTILATION", 25),
    )

