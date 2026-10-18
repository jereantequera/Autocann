"""
Indoor sensors: which ids the control loop reads, their roles and their max ages.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Indoor sensors
# ---------------------------------------------------------------------------

#: Redis key the single-sensor installation has always used. Kept as the key for
#: an unidentified poster so existing ESP32 firmware keeps working untouched.
LEGACY_INDOOR_KEY = "esp32_indoor"

#: A reading older than this is one the control loop refuses to act on, and the
#: dashboard marks stale. The sensor node posts every 10 s, so this tolerates
#: several lost cycles before a sensor is treated as gone.
INDOOR_SENSOR_MAX_AGE_SECONDS = 60

#: The roles an indoor sensor can hold. `PRIMARY` and `WITNESS` are also the
#: per-sensor keys of the drift monitor's report, which is why they live here
#: rather than in either of the modules that use them: a second spelling of
#: "witness" would stop matching the sensor the operator configured, and nothing
#: would say so.
PRIMARY = "primary"
WITNESS = "witness"

#: A sensor that is shown and nothing else: it does not control and it is not
#: cross-checked. It exists because a sensor can be worth reading while being
#: useless as a witness — this installation's `b` sits above the lamp, so the
#: offset between it and the primary swings with the photoperiod. Feeding that
#: to the drift monitor would alarm every time the light changes while hiding
#: real drift the rest of the time. Giving it a role instead of removing it is
#: what keeps it on the dashboard.
OBSERVER = "observer"

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
    the primary alone can detect. `observers` drive nothing and are not
    cross-checked either — they are read and shown, nothing more. Primary and
    witness unset means the legacy single-sensor installation, reading
    `esp32_indoor`.

    A witness and an observer are not interchangeable: the witness has to sit in
    the same air as the primary for the offset between them to be a constant of
    position. Put it somewhere else and what the detector measures is the
    environment, not the sensor.
    """

    primary: Optional[str] = None
    witness: Optional[str] = None
    observers: Tuple[str, ...] = ()

    @property
    def primary_key(self) -> str:
        return indoor_sensor_key(self.primary)

    @property
    def witness_key(self) -> Optional[str]:
        return indoor_sensor_key(self.witness) if self.witness else None

    def entries(self) -> Tuple[Tuple[str, Optional[str], str], ...]:
        """
        `(role, sensor_id, redis_key)` for each configured sensor, primary first.

        The single-sensor installation yields one entry whose id is None, so a
        caller that wants to show or read "every indoor sensor" never has to
        special-case the legacy key.
        """
        entries = [(PRIMARY, self.primary, self.primary_key)]
        witness_key = self.witness_key
        if witness_key is not None:
            entries.append((WITNESS, self.witness, witness_key))
        for observer in self.observers:
            entries.append((OBSERVER, observer, indoor_sensor_key(observer)))
        return tuple(entries)

    @property
    def has_witness(self) -> bool:
        """Whether drift detection is possible at all on this installation."""
        return self.witness is not None


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

    def _get_ids(name: str) -> Tuple[str, ...]:
        raw = (os.getenv(name) or "").strip().lower()
        ids = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if not is_valid_sensor_id(part):
                raise ValueError(
                    f"{name} contiene un id inválido: {part!r} "
                    "(minúsculas, dígitos, '_' y '-', hasta 16 caracteres)"
                )
            if part not in ids:
                ids.append(part)
        return tuple(ids)

    sensors = IndoorSensors(
        primary=_get_id("AUTOCANN_INDOOR_PRIMARY"),
        witness=_get_id("AUTOCANN_INDOOR_WITNESS"),
        observers=_get_ids("AUTOCANN_INDOOR_OBSERVERS"),
    )
    # A sensor listed twice would get two cards and, worse, two meanings: the
    # dashboard would show the same reading as both the thing control trusts and
    # the thing watching it.
    for observer in sensors.observers:
        if observer in (sensors.primary, sensors.witness):
            raise ValueError(
                f"AUTOCANN_INDOOR_OBSERVERS no puede repetir {observer!r}: "
                "ya es el primario o el testigo"
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
