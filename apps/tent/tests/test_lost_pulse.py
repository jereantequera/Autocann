"""
The lost-boot-pulse guard (autocann/runtime/lost_pulse.py).

The shape to catch is 2026-11-02 at 14:15: humidity_up on, the tent at 65 %
with its band starting at 66, and the needle following the room for hours
instead of the humidifier.
"""

from __future__ import annotations

from autocann.hardware.ir_remote import IR_PENDING_KEY, enqueue, find_button, node_payload
from autocann.runtime.lost_pulse import (
    MAX_PRESSES,
    STALE_OBSERVATION_SECONDS,
    LostPulseGuard,
)


class Recorder:
    def __init__(self, ok=True):
        self.sent, self.ok, self.lines = 0, ok, []

    def send(self):
        self.sent += 1
        return self.ok


def _guard(ok=True):
    rec = Recorder(ok)
    return LostPulseGuard(send_press=rec.send, log=rec.lines.append), rec


def _run(guard, readings, *, band_max=79.0, on=True, start=0.0, step=30.0):
    sent = []
    for i, rh in enumerate(readings):
        if guard.observe(humidity=rh, band_max=band_max,
                         humidifier_on=on, now=start + i * step):
            sent.append(i)
    return sent


def _room_drift(n, start=65.5):
    """What the tent did that afternoon: the room's own climb, nothing else."""
    return [start + i * 0.01 for i in range(n)]


def test_the_14_15_episode_sends_one_press():
    guard, rec = _guard()
    sent = _run(guard, _room_drift(16))        # eight minutes
    assert rec.sent == 1 and len(sent) == 1
    assert any("boot pulse was lost" in line for line in rec.lines)


def test_a_humidifier_that_works_is_left_alone():
    guard, rec = _guard()
    # Eight points in the first minute, which is what this one really does,
    # and then the loop switches the output off.
    _run(guard, [66, 70, 74, 74.5, 75])
    assert rec.sent == 0


def test_nothing_fires_before_the_boot_pulse_has_had_its_chance():
    """The first minutes are the node's to win; the guard is not a second pulse."""
    guard, rec = _guard()
    _run(guard, _room_drift(9))                # four and a half minutes
    assert rec.sent == 0


def test_nothing_fires_with_the_output_off():
    guard, rec = _guard()
    _run(guard, _room_drift(40), on=False)
    assert rec.sent == 0


def test_nothing_fires_once_the_tent_is_above_its_band():
    """
    Flat at the top of the band is a tent that got where it was asked to go,
    held on by an override. Pressing there would argue with whoever set it.
    """
    guard, rec = _guard()
    _run(guard, [80.0] * 40, band_max=79.0)
    assert rec.sent == 0


def test_the_output_going_off_resets_everything():
    """A new off→on transition gets its own boot pulse, which owns that cycle."""
    guard, rec = _guard()
    _run(guard, _room_drift(16))
    assert rec.sent == 1

    guard.observe(humidity=65.5, band_max=79.0, humidifier_on=False, now=600)
    _run(guard, _room_drift(9), start=630)     # under the grace again
    assert rec.sent == 1


def test_a_second_press_only_after_the_retry_wait_and_then_it_gives_up():
    guard, rec = _guard()
    # Half an hour of nothing: by now the presses are not what is going on.
    _run(guard, _room_drift(60))
    assert rec.sent == MAX_PRESSES
    assert any("not pressing more" in line for line in rec.lines)


def test_a_failed_queue_is_logged_and_not_counted():
    guard, rec = _guard(ok=False)
    _run(guard, _room_drift(16))
    assert rec.sent == 1
    assert any("could not be queued" in line for line in rec.lines)


def test_a_gap_in_the_feed_starts_the_clock_again():
    """
    Test mode, monitor mode and a stale reading all skip this guard. An "on
    since" from before one of those times a tent nobody was driving.
    """
    guard, rec = _guard()
    _run(guard, _room_drift(9))                # four and a half minutes, no press
    assert rec.sent == 0

    # The loop comes back after a gap longer than the guard tolerates.
    resumed = 9 * 30 + STALE_OBSERVATION_SECONDS + 1
    _run(guard, _room_drift(9), start=resumed)
    assert rec.sent == 0                       # under the grace once more

    _run(guard, _room_drift(16), start=resumed)
    assert rec.sent == 1


# The queue side


def test_the_press_reaches_the_node_as_an_ordinary_button(client):
    """
    Not a rescue: the output is on, and the node only sends a rescue with it
    off. Marking it would be the one way to guarantee it never goes out.
    """
    enqueue(client.redis, find_button("power"))
    payload = node_payload(client.redis)
    assert payload["code"] == "0x01FE48B7"
    assert "rescue" not in payload


def test_a_press_is_dropped_rather_than_stacked(client):
    """Two pulses undo each other, so the queue holds one."""
    first = enqueue(client.redis, find_button("power"))
    assert enqueue(client.redis, find_button("power")) is None
    assert client.redis.store[IR_PENDING_KEY].find(str(first["id"])) >= 0
