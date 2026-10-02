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
    """
    BCM pin numbers for the relay outputs.

    ⚠️  These defaults do NOT match what README.md used to claim. The README said
    humidificador=25 / ventilación=7; the code has always used the mapping
    below, so this is what your relays are actually wired to and it is left
    unchanged — swapping it would energise the wrong device. Verify it against
    your wiring once, then the documentation and the code agree.

    Override per installation with the AUTOCANN_PIN_* environment variables
    rather than editing this file.
    """

    humidity_up: int = 7
    humidity_down: int = 16
    ventilation: int = 25


def gpio_pins_from_env() -> GpioPins:
    """
    Pin mapping, overridable per installation without changing code.

    Raises ValueError if two outputs land on the same pin: they would switch
    together, so the humidifier and the dehumidifier could run at once.
    """
    defaults = GpioPins()

    def _get_int(name: str, default: int) -> int:
        val = os.getenv(name)
        if val is None or val.strip() == "":
            return default
        try:
            return int(val)
        except ValueError:
            raise ValueError(f"{name}={val!r} is not a valid BCM pin number") from None

    pins = GpioPins(
        humidity_up=_get_int("AUTOCANN_PIN_HUMIDITY_UP", defaults.humidity_up),
        humidity_down=_get_int("AUTOCANN_PIN_HUMIDITY_DOWN", defaults.humidity_down),
        ventilation=_get_int("AUTOCANN_PIN_VENTILATION", defaults.ventilation),
    )

    assigned = [pins.humidity_up, pins.humidity_down, pins.ventilation]
    if len(set(assigned)) != len(assigned):
        raise ValueError(
            f"Two outputs share a GPIO pin: humidity_up={pins.humidity_up}, "
            f"humidity_down={pins.humidity_down}, ventilation={pins.ventilation}"
        )

    return pins



@dataclass(frozen=True)
class ControlTuning:
    """
    Timing limits for the humidity controller.

    These protect the hardware, so they are tunable per installation without
    editing code. A dehumidifier with a compressor wants a longer `min_off`
    (300s is a reasonable floor); a small ultrasonic humidifier tolerates less.
    """

    min_on_seconds: float = 60.0
    min_off_seconds: float = 180.0
    min_changeover_seconds: float = 120.0
    stale_after_seconds: float = 120.0


def control_tuning_from_env() -> ControlTuning:
    defaults = ControlTuning()

    def _get_float(name: str, default: float) -> float:
        val = os.getenv(name)
        if val is None or val.strip() == "":
            return default
        try:
            parsed = float(val)
        except ValueError:
            raise ValueError(f"{name}={val!r} is not a number") from None
        if parsed < 0:
            raise ValueError(f"{name}={val!r} must not be negative")
        return parsed

    return ControlTuning(
        min_on_seconds=_get_float("AUTOCANN_MIN_ON_SECONDS", defaults.min_on_seconds),
        min_off_seconds=_get_float("AUTOCANN_MIN_OFF_SECONDS", defaults.min_off_seconds),
        min_changeover_seconds=_get_float(
            "AUTOCANN_MIN_CHANGEOVER_SECONDS", defaults.min_changeover_seconds
        ),
        stale_after_seconds=_get_float(
            "AUTOCANN_STALE_AFTER_SECONDS", defaults.stale_after_seconds
        ),
    )
