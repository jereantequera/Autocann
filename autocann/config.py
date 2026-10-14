from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Optional, Tuple


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


def _pin_from_env(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    try:
        return int(val)
    except ValueError:
        raise ValueError(f"{name}={val!r} is not a valid pin number") from None


def _reject_shared_pins(pins: Dict[str, int], *, board: str) -> None:
    """
    Raise if two outputs land on the same pin of `board`.

    They would switch together, so the humidifier and the dehumidifier could
    run at once. True of the Raspberry's header, and just as true of the remote
    node's: the hazard belongs to the pin, not to the board.
    """
    if len(set(pins.values())) == len(pins):
        return
    detail = ", ".join(f"{name}={pin}" for name, pin in pins.items())
    raise ValueError(f"Two outputs share a pin on {board}: {detail}")


@dataclass(frozen=True)
class GpioPins:
    """
    BCM pin numbers for the relay outputs, on the Raspberry's own header.

    These matter only in RELAY_MODE_GPIO. With the relays on a remote node it
    is `RelayNode` that holds the pins in the circuit, and nothing is wired to
    the numbers below — see `autocann.hardware.outputs.pin_sources()`.

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
    """Pin mapping, overridable per installation without changing code."""
    defaults = GpioPins()
    pins = GpioPins(
        humidity_up=_pin_from_env("AUTOCANN_PIN_HUMIDITY_UP", defaults.humidity_up),
        humidity_down=_pin_from_env("AUTOCANN_PIN_HUMIDITY_DOWN", defaults.humidity_down),
        ventilation=_pin_from_env("AUTOCANN_PIN_VENTILATION", defaults.ventilation),
    )
    _reject_shared_pins(asdict(pins), board="the Raspberry's header")
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


# ---------------------------------------------------------------------------
# Pause
# ---------------------------------------------------------------------------

#: Redis key mirroring the pause.
#:
#: Holds the timestamp the pause *started*, and has no TTL. It used to hold a
#: deadline that expired on its own after eight hours, on the argument that a
#: flag someone forgets leaves the tent uncontrolled with nothing to say so.
#: The argument was right about the failure and wrong about the fix: a pause
#: that resumes by itself is worse, because it energises relays at a moment
#: nobody chose - which, if the pause was taken to rewire something, is while
#: somebody's hands are in there. What answers the original worry is that the
#: state is now impossible to miss: the header says it, in amber, on every view,
#: for as long as it lasts.
TEST_MODE_KEY = "test_mode_since"

#: Redis key holding what the operator asked each output to do while paused,
#: as {name: bool}. Deliberately *not* the manual-override keys the automatic
#: path reads: those would still be sitting there when the pause ends, and
#: would then quietly override real control decisions.
TEST_MODE_OUTPUTS_KEY = "test_mode_outputs"

#: Settings key mirroring the pause into the database.
#:
#: Redis stays the fast path - the loop reads it every cycle - but it is not the
#: truth any more. Redis is memory: a restart of it, or of the Raspberry, used
#: to mean the tent silently went back to being controlled. Someone who paused
#: it does not expect a reboot to undo that, so the database carries the state
#: and the loop restores the mirror from it.
TEST_MODE_SETTING = "paused"


# ---------------------------------------------------------------------------
# Relay outputs
# ---------------------------------------------------------------------------

#: Relays driven from this process's own GPIO pins.
RELAY_MODE_GPIO = "gpio"
#: Relays driven by a remote node that polls for the desired state. The loop
#: stops touching GPIO entirely and only publishes what it wants.
RELAY_MODE_REMOTE = "remote"

RELAY_MODES = (RELAY_MODE_GPIO, RELAY_MODE_REMOTE)

#: Redis key holding the full desired output state. A remote relay node
#: polls it; the poll is both the command and the keepalive, so this is
#: rewritten every cycle even when nothing changed. Silence is the signal
#: that tells the node the loop is gone.
DESIRED_OUTPUTS_KEY = "desired_outputs"

#: A desired-output state older than this means the control loop is not
#: running. Roughly ten of its cycles: long enough that a slow iteration or
#: a blocked sensor read never trips it, short enough to matter.
#:
#: Both of these live here, rather than beside the loop that writes the key or
#: the endpoint that serves it, because three modules now have to agree on
#: them: the loop publishes, `GET /api/outputs/desired` decides from the age
#: whether to answer failsafe, and the system check reports whether a polling
#: node is getting orders at all. A check reading a different key, or allowing
#: a different age, would pass while the node sat in failsafe.
DESIRED_OUTPUTS_MAX_AGE_SECONDS = 30


def relay_mode_from_env() -> str:
    mode = (os.getenv("AUTOCANN_RELAY_MODE") or RELAY_MODE_GPIO).strip().lower()
    if mode not in RELAY_MODES:
        raise ValueError(
            f"AUTOCANN_RELAY_MODE={mode!r} must be one of {', '.join(RELAY_MODES)}"
        )
    return mode


@dataclass(frozen=True)
class RelayNodePins:
    """
    The *node's* own GPIO numbers — not the Raspberry's BCM pins.

    They live in the backend's configuration because the node never says what
    they are: it only polls `GET /api/outputs/desired` and applies the answer,
    so there is no report to read them off. Teaching it to report its wiring
    means reflashing it over USB, in the tent, which is a trip with a cable and
    not a deploy — so until someone makes that trip the authority is
    `firmware/relay_node/relay_node.ino` and these defaults copy it verbatim.
    `tests/test_relays.py` reads the firmware and fails if the two drift apart.
    """

    humidity_up: int = 25
    humidity_down: int = 26
    ventilation: int = 27


@dataclass(frozen=True)
class RelayNode:
    """Which board switches the relays in RELAY_MODE_REMOTE, and on what pins."""

    #: Named on the dashboard, so it has to be enough to walk up to the right
    #: board: this tent has two ESP32s and only one of them switches anything.
    #: No address in the default, because nothing here talks to the node — the
    #: node polls, precisely so the Pi never needs its IP — so this is a label,
    #: and a default carrying an invented IP would be a label that lies on day
    #: one. Set AUTOCANN_RELAY_NODE_NAME to put the real address on screen.
    name: str = "ESP32 relés"
    pins: RelayNodePins = field(default_factory=RelayNodePins)


def relay_node_from_env() -> RelayNode:
    """The remote node's identity and pin mapping, overridable per installation."""
    defaults = RelayNodePins()
    pins = RelayNodePins(
        humidity_up=_pin_from_env("AUTOCANN_RELAY_NODE_PIN_HUMIDITY_UP", defaults.humidity_up),
        humidity_down=_pin_from_env("AUTOCANN_RELAY_NODE_PIN_HUMIDITY_DOWN", defaults.humidity_down),
        ventilation=_pin_from_env("AUTOCANN_RELAY_NODE_PIN_VENTILATION", defaults.ventilation),
    )
    _reject_shared_pins(asdict(pins), board="the relay node")
    name = (os.getenv("AUTOCANN_RELAY_NODE_NAME") or "").strip()
    return RelayNode(name=name or RelayNode().name, pins=pins)
