from __future__ import annotations

import pytest

from autocann.control.humidity import (
    DEHUMIDIFY,
    HUMIDIFY,
    IDLE,
    ControllerConfig,
    ControllerState,
    MedianFilter,
    decide,
)

CFG = ControllerConfig(
    deadband_pct=2.0, min_on_seconds=60.0, min_off_seconds=180.0,
    min_changeover_seconds=120.0,
)


def run(state, humidity, now, target=60.0, config=CFG, age=0.0):
    return decide(
        humidity=humidity,
        target_humidity=target,
        now=now,
        state=state,
        config=config,
        reading_age_seconds=age,
    )


def test_noise_inside_deadband_never_starts_a_device():
    state = ControllerState()
    # ±2% jitter around the setpoint, sampled every 3s like the real loop.
    for i, humidity in enumerate([60.0, 61.5, 58.6, 61.9, 58.2, 60.4]):
        decision = run(state, humidity, now=i * 3)
        assert decision.action == IDLE
        assert decision.reason == "in_deadband"
        assert not decision.changed


def test_starts_humidifier_only_once_outside_deadband():
    state = ControllerState()
    assert run(state, 58.5, now=0).action == IDLE          # 1.5% low, inside deadband
    decision = run(state, 55.0, now=3)                      # 5% low, outside deadband
    assert (decision.action, decision.changed) == (HUMIDIFY, True)


def test_starts_dehumidifier_when_too_humid():
    state = ControllerState()
    decision = run(state, 70.0, now=0)
    assert (decision.action, decision.changed) == (DEHUMIDIFY, True)


def test_holds_until_target_is_crossed_not_until_deadband_edge():
    state = ControllerState()
    run(state, 50.0, now=0)
    # Inside the deadband again, but the device must keep running to the setpoint.
    decision = run(state, 59.0, now=90)
    assert decision.action == HUMIDIFY
    assert decision.reason == "still_correcting"


def test_minimum_on_time_prevents_short_cycling():
    state = ControllerState()
    run(state, 50.0, now=0)                      # humidifier starts
    decision = run(state, 65.0, now=10)          # target overshot after only 10s
    assert decision.action == HUMIDIFY
    assert decision.reason == "min_on_time"

    decision = run(state, 65.0, now=61)          # past the minimum on time
    assert (decision.action, decision.reason) == (IDLE, "target_reached")


def test_minimum_off_time_blocks_immediate_restart_of_same_device():
    state = ControllerState()
    run(state, 50.0, now=0)                      # humidifier on
    run(state, 61.0, now=61)                     # reached target -> off at t=61

    # Past the changeover delay, so only this device's own off timer can block it.
    decision = run(state, 50.0, now=61 + CFG.min_changeover_seconds + 1)
    assert (decision.action, decision.reason) == (IDLE, "anti_short_cycle")

    decision = run(state, 50.0, now=61 + CFG.min_off_seconds + 1)
    assert (decision.action, decision.changed) == (HUMIDIFY, True)


def test_the_opposite_device_waits_for_the_changeover_delay():
    """
    The humidifier stopping must not let the dehumidifier start seconds later;
    the two would spend the day undoing each other's work.
    """
    state = ControllerState()
    run(state, 50.0, now=0)                      # humidifier on
    run(state, 61.0, now=61)                     # humidifier off at t=61
    decision = run(state, 70.0, now=70)          # too humid 9s later
    assert (decision.action, decision.reason) == (IDLE, "changeover_delay")

    # Allowed once the changeover delay has passed — and the dehumidifier has
    # its own min_off timer untouched, so it is not blocked twice.
    decision = run(state, 70.0, now=61 + CFG.min_changeover_seconds)
    assert (decision.action, decision.changed) == (DEHUMIDIFY, True)


def test_humidify_and_dehumidify_are_mutually_exclusive():
    state = ControllerState()
    for now, humidity in enumerate([50.0, 70.0, 50.0, 70.0]):
        decision = run(state, humidity, now=now * 200)
        assert not (decision.humidify and decision.dehumidify)


@pytest.mark.parametrize(
    "humidity, target, age, expected_reason",
    [
        (None, 60.0, 0.0, "no_data"),
        (55.0, None, 0.0, "no_data"),
        (55.0, 60.0, 999.0, "stale_data"),
    ],
)
def test_failsafe_stops_everything_and_ignores_minimum_on_time(humidity, target, age, expected_reason):
    state = ControllerState()
    run(state, 50.0, now=0)                      # humidifier running
    decision = decide(
        humidity=humidity,
        target_humidity=target,
        now=5,                                   # well inside min_on_seconds
        state=state,
        config=CFG,
        reading_age_seconds=age,
    )
    assert (decision.action, decision.reason, decision.changed) == (IDLE, expected_reason, True)


def test_median_filter_rejects_an_isolated_spike():
    f = MedianFilter(size=5)
    for value in (60.0, 60.5, 61.0, 60.2):
        f.push(value)
    assert f.push(95.0) == pytest.approx(60.5)   # spike does not move the median
    assert f.ready


def test_median_filter_is_empty_until_fed_and_ignores_none():
    f = MedianFilter(size=3)
    assert f.value is None
    assert f.push(None) is None
    assert f.push(50.0) == 50.0
    assert not f.ready


def test_adversarial_noise_cannot_switch_faster_than_the_minimum_cycle():
    """
    The old loop had no floor on switching rate: a sensor flipping either side of
    the target could toggle a relay on every 3-second iteration (28,800 times a
    day). Whatever the readings do, switches must respect min_on/min_off.
    """
    import random

    rnd = random.Random(1234)
    state = ControllerState()
    switch_times = []
    previous = IDLE

    for step in range(2000):                      # 100 minutes at 3s per step
        now = step * 3.0
        # Worst case for a bang-bang controller: readings straddling the target.
        humidity = 60.0 + rnd.choice([-12.0, 12.0]) + rnd.gauss(0, 4)
        decision = run(state, humidity, now=now)
        if decision.action != previous:
            switch_times.append(now)
            previous = decision.action

    gaps = [b - a for a, b in zip(switch_times, switch_times[1:])]
    assert gaps, "expected the controller to act at all"
    assert min(gaps) >= CFG.min_on_seconds, f"switched after only {min(gaps)}s"

    # A full cycle costs at least min_on + min_changeover, and each cycle is two
    # switches. The old loop's floor was one iteration: 3 seconds.
    duration = 2000 * 3.0
    cycle_floor = CFG.min_on_seconds + CFG.min_changeover_seconds
    assert len(switch_times) <= 2 * duration / cycle_floor + 1


def test_a_device_always_runs_for_at_least_the_minimum_on_time():
    import random

    rnd = random.Random(99)
    state = ControllerState()
    started_at = None
    previous = IDLE

    for step in range(2000):
        now = step * 3.0
        decision = run(state, 60.0 + rnd.uniform(-15, 15), now=now)
        if decision.action != previous:
            if previous in (HUMIDIFY, DEHUMIDIFY):
                assert now - started_at >= CFG.min_on_seconds
            started_at = now
            previous = decision.action


def test_failsafe_decisions_are_flagged_so_the_loop_can_ignore_overrides():
    state = ControllerState()
    assert decide(humidity=None, target_humidity=60.0, now=0, state=state, config=CFG).failsafe
    assert decide(
        humidity=55.0, target_humidity=60.0, now=0, state=state, config=CFG,
        reading_age_seconds=9999,
    ).failsafe


def test_normal_decisions_are_not_flagged_as_failsafe():
    state = ControllerState()
    assert not run(state, 50.0, now=0).failsafe        # correcting
    assert not run(state, 50.0, now=3).failsafe        # still_correcting
    assert not run(state, 60.0, now=1000).failsafe     # target_reached
    assert not run(state, 60.0, now=2000).failsafe     # in_deadband
