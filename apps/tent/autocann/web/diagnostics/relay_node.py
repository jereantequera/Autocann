"""
The relay node's poll, written down — the only trace that board leaves anywhere.

The sensor node POSTs its status and its log, so there is something to read
about it even when it is unhappy. The relay node was built to need no such
channel: it polls `GET /api/outputs/desired` every two seconds, applies the
answer, and says nothing. The poll *is* the heartbeat, and the firmware's
comments are right that this covers WiFi, the Pi, the backend and the cable
with one mechanism.

What it did not cover was the backend noticing. The poll arrived, was served
and was thrown away, so nothing on this side could tell a board applying its
commands from a board that had been off the air since yesterday evening. On
2026-11-10 at 18:14 that stopped being theoretical: the panel went on drawing
a dehumidifier nobody was switching, and `/api/health` answered "Todo en
orden" for the fifteen hours that followed.

So the poll is recorded here, with whatever the node chose to say about itself
on the way past. Reading and judging it is `nodes.relay_node_verdict`; this
module is only the I/O and the arithmetic that needs two polls to see.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, Optional

from autocann.config import (
    RELAY_NODE_LAST_POLL_KEY,
    relay_mode_from_env,
    relay_node_from_env,
)
from autocann.time import ARGENTINA_TZ

from .health import _as_int
from .nodes import BOOT_REASONS, relay_node_verdict

#: How many of the node's boots to remember.
#:
#: Enough to tell a restart from a loop over the window the verdict asks about
#: and no more: this rides a key that is rewritten every two seconds, and a
#: list that grew without bound would be the one part of it that costs
#: something.
BOOTS_KEPT = 8


#: Bounds for the numbers the node reports. The endpoint that collects them has
#: no authentication, so these are somebody's input: a value outside its range
#: is dropped rather than drawn. An absent number reads as "not known", which
#: is honest; a wrong one would not be.
RSSI_RANGE = (-120, 0)
UPTIME_MAX_SECONDS = 10 * 365 * 24 * 3600


def _now() -> int:
    return int(datetime.now(ARGENTINA_TZ).timestamp())


def _bounded(value, low, high) -> Optional[int]:
    number = _as_int(value)
    if number is None or not low <= number <= high:
        return None
    return number


def _rebooted(previous: Dict, boot_reason, uptime_seconds) -> bool:
    """
    Whether this poll comes from a node that restarted since the last one.

    Uptime going backwards, rather than arithmetic on the boot time: the node
    counts in `millis()` and the Pi in wall clock, so a boot time computed from
    the two drifts a second or so between polls, and a restart after a short
    uptime would hide inside that drift. A counter that decreases cannot.

    A first sighting counts as a boot. It is a boot that happened and that
    nobody saw, and the alternative is a panel that claims the node has been up
    since the backend was deployed.
    """
    before = _as_int(previous.get("uptime_seconds"))
    if before is None:
        return True
    if uptime_seconds < before:
        return True
    return bool(boot_reason) and boot_reason != previous.get("boot_reason")


def _boots(previous: Dict, now: int, boot_reason, uptime_seconds):
    kept = [b for b in (previous.get("boots") or []) if isinstance(b, int)][-BOOTS_KEPT:]
    if uptime_seconds is None:
        # Firmware from before 2026-11-11 reports nothing, so there is no
        # uptime to derive a boot from. Whatever is already in the list still
        # happened, so it stays.
        return kept
    if not _rebooted(previous, boot_reason, uptime_seconds):
        return kept
    return (kept + [max(0, now - uptime_seconds)])[-BOOTS_KEPT:]


def read_poll(redis_client) -> Optional[Dict]:
    """The last recorded poll, or None when there is none to read."""
    try:
        raw = redis_client.get(RELAY_NODE_LAST_POLL_KEY)
    except Exception:
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def record_poll(redis_client, ip, *, boot_reason=None, uptime_seconds=None,
                rssi=None, wifi_reconnects=None) -> None:
    """
    Note that the relay node asked what to do, from where, and how it is.

    No TTL. An expired key reads as "never polled", and the useful thing to
    know about a node that stopped three days ago is that it stopped three days
    ago. Never fatal either: the node's orders matter more than the bookkeeping,
    so a Redis that is refusing writes must not cost the tent its control.
    """
    now = _now()
    previous = read_poll(redis_client) or {}
    uptime = _bounded(uptime_seconds, 0, UPTIME_MAX_SECONDS)
    slug = boot_reason if boot_reason in BOOT_REASONS else None
    try:
        redis_client.set(RELAY_NODE_LAST_POLL_KEY, json.dumps({
            "at": now,
            "ip": ip or None,
            "boot_reason": slug,
            "uptime_seconds": uptime,
            "rssi": _bounded(rssi, *RSSI_RANGE),
            "wifi_reconnects": _bounded(wifi_reconnects, 0, 10 ** 9),
            "boots": _boots(previous, now, slug, uptime),
        }))
    except Exception:
        pass


def record_poll_from_request(redis_client, args, ip) -> None:
    """
    Record a poll, taking the node's own report off its query string.

    A GET that writes, which is the price of the node having no other channel:
    giving it a POST of its own would mean a second timer that can drift out of
    step with the first, and not drifting is the whole reason this node only
    ever polls.
    """
    record_poll(
        redis_client,
        ip,
        boot_reason=args.get("boot"),
        uptime_seconds=args.get("up"),
        rssi=args.get("rssi"),
        wifi_reconnects=args.get("rc"),
    )


def status(redis_client, now: Optional[int] = None) -> Optional[Dict]:
    """
    What the board behind the relays is doing, or None when none is involved.

    Served beside the outputs rather than from an endpoint of its own because
    it is the same fact: an output's `state` is a request, and this says whether
    anything is on the other end of it. Two polls could disagree, and the gap
    between them is precisely the lie this is here to stop telling.
    """
    try:
        return relay_node_verdict(
            read_poll(redis_client),
            _now() if now is None else now,
            mode=relay_mode_from_env(),
            name=relay_node_from_env().name,
        )
    except ValueError:
        # A misconfigured AUTOCANN_RELAY_* is its own fault with its own
        # message; it must not take the outputs answer down with it.
        return None
