from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional


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


# ---------------------------------------------------------------------------
# Indoor sensors
# ---------------------------------------------------------------------------

#: Redis key the single-sensor installation has always used. Kept as the key for
#: an unidentified poster so existing ESP32 firmware keeps working untouched.
LEGACY_INDOOR_KEY = "esp32_indoor"

#: Sensor ids are interpolated into Redis key names, and `/api/sensor/indoor`
#: has no authentication (see ROADMAP). Without this, a poster on the LAN could
#: put a ':' in the id and write to any key the dashboard or the control loop
#: reads.
_SENSOR_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,15}$")


def is_valid_sensor_id(sensor_id: str) -> bool:
    return bool(_SENSOR_ID_PATTERN.match(sensor_id))


def indoor_sensor_key(sensor_id: Optional[str]) -> str:
    """
    Redis key holding one indoor sensor's latest reading.

    Shared by the web endpoint that writes it and the control loop that reads
    it, so the two cannot drift apart on the naming.
    """
    if not sensor_id:
        return LEGACY_INDOOR_KEY
    if not is_valid_sensor_id(sensor_id):
        raise ValueError(f"invalid sensor id: {sensor_id!r}")
    return f"{LEGACY_INDOOR_KEY}:{sensor_id}"


@dataclass(frozen=True)
class IndoorSensors:
    """
    Which indoor sensors the control loop should read.

    `primary` drives control. `witness` drives nothing: it exists only so the
    primary can be caught reporting a plausible wrong number, which no filter on
    the primary alone can detect. Both unset means the legacy single-sensor
    installation, reading `esp32_indoor`.
    """

    primary: Optional[str] = None
    witness: Optional[str] = None

    @property
    def primary_key(self) -> str:
        return indoor_sensor_key(self.primary)

    @property
    def witness_key(self) -> Optional[str]:
        return indoor_sensor_key(self.witness) if self.witness else None


def indoor_sensors_from_env() -> IndoorSensors:
    def _get_id(name: str) -> Optional[str]:
        raw = (os.getenv(name) or "").strip().lower()
        if not raw:
            return None
        if not is_valid_sensor_id(raw):
            raise ValueError(
                f"{name}={raw!r} is not a valid sensor id "
                "(lowercase letters, digits, '_' and '-', up to 16 chars)"
            )
        return raw

    sensors = IndoorSensors(
        primary=_get_id("AUTOCANN_INDOOR_PRIMARY"),
        witness=_get_id("AUTOCANN_INDOOR_WITNESS"),
    )
    if sensors.witness and sensors.witness == sensors.primary:
        raise ValueError(
            "AUTOCANN_INDOOR_WITNESS must differ from AUTOCANN_INDOOR_PRIMARY: "
            "a sensor cannot be its own cross-check"
        )
    if sensors.witness and not sensors.primary:
        raise ValueError(
            "AUTOCANN_INDOOR_WITNESS needs AUTOCANN_INDOOR_PRIMARY set too"
        )
    return sensors
