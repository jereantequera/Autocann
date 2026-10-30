"""
The stuck-humidifier-relay patch (autocann/runtime/stuck_relay.py).

The shape to catch is 2026-10-29 at 17:23: humidity_up off, and the tent going
from 61 % to 97 % in a quarter of an hour.
"""

from __future__ import annotations

import json

from autocann.hardware.ir_remote import IR_PENDING_KEY, enqueue, enqueue_rescue, find_button, node_payload
from autocann.runtime.stuck_relay import MAX_RESCUES, StuckRelayGuard


class Recorder:
    def __init__(self, ok=True):
        self.sent, self.ok, self.lines = 0, ok, []

    def send(self):
        self.sent += 1
        return self.ok


def _guard(ok=True):
    rec = Recorder(ok)
    return StuckRelayGuard(send_rescue=rec.send, log=rec.lines.append), rec


def _run(guard, readings, *, band_max=71.0, on=False, start=0.0, step=30.0):
    sent = []
    for i, rh in enumerate(readings):
        if guard.observe(humidity=rh, band_max=band_max, humidifier_on=on, now=start + i * step):
            sent.append(i)
    return sent


def test_the_17_23_episode_sends_one_rescue():
    guard, rec = _guard()
    # Every 30 s: off at t=0, then climbing 3 points a reading.
    sent = _run(guard, [61, 62, 64, 67, 70, 73, 76, 79, 81, 82, 82, 82])
    assert rec.sent == 1 and len(sent) == 1


def test_a_slow_drift_above_the_band_does_not_fire():
    guard, rec = _guard()
    _run(guard, [72 + i * 0.3 for i in range(40)])
    assert rec.sent == 0


def test_nothing_fires_inside_the_band():
    guard, rec = _guard()
    _run(guard, [50 + i * 2 for i in range(10)], band_max=71.0)  # 50 → 68
    assert rec.sent == 0


def test_nothing_fires_with_the_humidifier_on():
    guard, rec = _guard()
    _run(guard, [61 + i * 3 for i in range(12)], on=True)
    assert rec.sent == 0


def test_nothing_fires_right_after_switching_off():
    """A working humidifier's mist still lands for a moment after it stops."""
    guard, rec = _guard()
    _run(guard, [70, 74, 78, 82], step=30.0)  # all within the first 2 minutes
    assert rec.sent == 0


def test_a_second_rescue_only_after_the_retry_wait_and_then_it_gives_up():
    guard, rec = _guard()
    # Keeps rising for 20 minutes: the rescues are not what is going on.
    _run(guard, [72 + i * 1.5 for i in range(40)])
    assert rec.sent == MAX_RESCUES
    assert any("not sending more" in line for line in rec.lines)


def test_turning_the_humidifier_on_resets_the_count():
    guard, rec = _guard()
    _run(guard, [72 + i * 1.5 for i in range(40)])
    guard.observe(humidity=60, band_max=71, humidifier_on=True, now=2000)
    _run(guard, [72 + i * 1.5 for i in range(12)], start=2030)
    assert rec.sent == MAX_RESCUES + 1


def test_a_failed_queue_is_logged_and_not_counted():
    guard, rec = _guard(ok=False)
    _run(guard, [61, 62, 64, 67, 70, 73, 76, 79, 81, 82])
    assert rec.sent == 1
    assert any("could not be queued" in line for line in rec.lines)


# The queue side


def test_a_rescue_reaches_the_node_marked_as_such(client):
    command = enqueue_rescue(client.redis)
    assert node_payload(client.redis) == {"id": command["id"], "code": "0x01FE48B7", "rescue": True}


def test_a_rescue_replaces_a_pending_press(client):
    enqueue(client.redis, find_button("fog"))
    rescue = enqueue_rescue(client.redis)
    assert json.loads(client.redis.store[IR_PENDING_KEY])["id"] == rescue["id"]


def test_a_normal_press_is_not_marked_rescue(client):
    enqueue(client.redis, find_button("power"))
    assert "rescue" not in node_payload(client.redis)
