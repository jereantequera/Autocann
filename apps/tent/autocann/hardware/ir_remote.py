"""
The humidifier's IR remote: its buttons, and the one-command queue the web
remote uses to reach the relay node.

The node is the only thing with an IR LED, and nothing talks to the node — it
polls `GET /api/outputs/desired` — so a button pressed on the dashboard is
parked here until the node's next poll picks it up, and the node says it sent
it by acknowledging the id on the poll after.

The queue holds ONE command, on purpose. POWER is a toggle (see
firmware/relay_node/CONTROL_REMOTO.md): two pulses turn the humidifier off, so
a press must be delivered at most once, and a second press while the first is
still in flight is refused rather than stacked or merged. A command nobody
picked up expires instead of firing late, against a state nobody is looking
at any more.

Free of Flask, like the rest of `autocann.hardware`: it takes the Redis client.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class IrButton:
    key: str
    label: str
    #: NEC, 32 bits, as captured: `irsend.sendNEC(code)` on the node.
    code: int
    #: What the button does, in a line, for the dashboard.
    hint: str


#: In the remote's own order. Codes from firmware/relay_node/CONTROL_REMOTO.md;
#: `tests/test_ir_remote.py` checks POWER against the one the firmware sends by
#: itself on every power-up, so the two cannot drift apart.
IR_BUTTONS = (
    IrButton("power", "ON / OFF", 0x01FE48B7, "Alterna: si estaba prendido, lo apaga"),
    IrButton("fog", "Niebla", 0x01FE7887, "Cambia la cantidad de niebla"),
    IrButton("constant", "Constante", 0x01FE20DF, "Modo humedad constante"),
    IrButton("purify", "Purificar", 0x01FE609F, "Modo purificación"),
    IrButton("timing", "Timer", 0x01FE50AF, "Temporizador del equipo"),
    IrButton("screen_off", "Pantalla", 0x01FEF807, "Apaga la pantalla del equipo"),
)

#: The output that powers the humidifier. With it off the equipment has no
#: power and no pulse can reach it, so a press is refused instead of queued.
IR_TARGET_OUTPUT = "humidity_up"

IR_PENDING_KEY = "ir_remote:pending"
IR_LAST_KEY = "ir_remote:last"
IR_SEQ_KEY = "ir_remote:seq"

#: How long a press waits for the node. Several polls (the node polls every
#: 2 s), and short enough that a press that did not go out is reported as such
#: while the person is still standing there, not delivered half a minute later.
IR_COMMAND_TTL_SECONDS = 10

#: How long the last delivered press is remembered, for the dashboard's line.
IR_LAST_TTL_SECONDS = 10 * 60


def find_button(key: str) -> Optional[IrButton]:
    return next((b for b in IR_BUTTONS if b.key == key), None)


def _load(raw) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def pending_command(redis) -> Optional[Dict[str, Any]]:
    return _load(redis.get(IR_PENDING_KEY))


def last_sent(redis) -> Optional[Dict[str, Any]]:
    return _load(redis.get(IR_LAST_KEY))


def enqueue(redis, button: IrButton) -> Optional[Dict[str, Any]]:
    """Park a press for the node. None if another one is still in flight."""
    if pending_command(redis):
        return None
    command = {
        "id": int(redis.incr(IR_SEQ_KEY)),
        "button": button.key,
        "code": button.code,
        "queued_at": int(time.time()),
    }
    redis.set(IR_PENDING_KEY, json.dumps(command), ex=IR_COMMAND_TTL_SECONDS)
    return command


def acknowledge(redis, raw_ack) -> None:
    """
    The node says it sent command `raw_ack`: clear it and remember it.

    An ack for anything but the pending id changes nothing. The node repeats
    its last id on every poll, so this is called with old ids all the time.
    """
    try:
        ack = int(raw_ack)
    except (TypeError, ValueError):
        return
    command = pending_command(redis)
    if not command or command.get("id") != ack:
        return
    redis.delete(IR_PENDING_KEY)
    redis.set(
        IR_LAST_KEY,
        json.dumps({**command, "sent_at": int(time.time())}),
        ex=IR_LAST_TTL_SECONDS,
    )


def node_payload(redis) -> Optional[Dict[str, Any]]:
    """What goes in the node's poll answer: the pending press, or None."""
    command = pending_command(redis)
    if not command:
        return None
    # Hex, as in the capture notes, and so the node can parse it with
    # strtoul(…, 16) without caring that it does not fit a signed long.
    return {"id": command["id"], "code": f"0x{int(command['code']):08X}"}
