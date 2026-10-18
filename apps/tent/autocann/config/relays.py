"""
Relay outputs: how they are driven (GPIO or a remote node), which pins they use
and the remote node's identity.

The pins of the Raspberry's own header and of the relay node are separate
mappings on purpose: see `GpioPins` and `RelayNodePins`.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Dict


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
