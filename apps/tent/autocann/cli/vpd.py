#!/usr/bin/env python
"""
VPD / humidity control loop.

Reads the indoor sensor (an ESP32 posting to the web API, or a local DHT22),
decides what the humidity outputs should do via `autocann.control.humidity`,
and drives the relays.

All interval timing uses a monotonic clock. A Raspberry Pi without a real-time
clock gets the correct wall time from NTP only after it is online, so wall-clock
deltas can jump by hours and must never gate control decisions.
"""

from __future__ import annotations

import argparse
import json
import signal
from dataclasses import replace
from datetime import datetime
from time import monotonic, sleep
from typing import Any, Dict, FrozenSet, Optional

from autocann.config import (
    MODE_MONITOR,
    RELAY_MODE_REMOTE,
    UNCONTROLLED_MODES,
    control_tuning_from_env,
    indoor_sensors_from_env,
    relay_mode_from_env,
)
from autocann.control.humidity import (
    DEHUMIDIFY,
    HUMIDIFY,
    IDLE,
    ControllerConfig,
    ControllerState,
    decide,
)
from autocann.control.quiet import blocked_actions, windows_from_dict
from autocann.control.sampling import QUALITY_DEGRADED, QUALITY_FAILSAFE, QUALITY_OK, SampleAccumulator
from autocann.control.vpd_math import LEAF_TEMP_OFFSET_C, STAGES
from autocann.db import ensure_schema, get_active_grow, get_setting
from autocann.hardware.dht import DHT_READ_ATTEMPTS, DHT_RETRY_DELAY_SECONDS
from autocann.hardware.outdoor import OUTDOOR_MAX_AGE_SECONDS
from autocann.hardware.sensors import Sensors
from autocann.runtime.clock import _now_local
from autocann.runtime.loop import (
    LOOP_INTERVAL_SECONDS,
    QUIET_HOURS_SETTING,
    SENSOR_REINIT_INTERVAL_SECONDS,
    SETTINGS_CHECK_INTERVAL_SECONDS,
    STAGE_CHECK_INTERVAL_SECONDS,
    flush_history,
    run_monitor_cycle,
    run_test_mode_cycle,
)
from autocann.runtime.mode import parse_mode_mirror, read_control_mode, restore_pause_mirror
from autocann.runtime.outputs import Relays, read_manual_overrides
from autocann.runtime.redis import _set_redis
from autocann.runtime.setpoint import resolve_setpoint
from autocann.runtime.stage_band import StageBand
from autocann.runtime.stuck_relay import humidifier_rescue_guard
from autocann.time import ARGENTINA_TZ

# Names the tests and `autocann.cli.simulate` import from this module. Re-exported on purpose.
__all__ = [
    "main", "build_parser", "Relays", "Sensors", "resolve_setpoint", "parse_mode_mirror",
    "DHT_READ_ATTEMPTS", "DHT_RETRY_DELAY_SECONDS", "OUTDOOR_MAX_AGE_SECONDS", "LEAF_TEMP_OFFSET_C",
]


_shutdown = False


def _request_shutdown(signum, _frame) -> None:
    global _shutdown
    print(f"\n🛑 Signal {signum} received, shutting down...")
    _shutdown = True


def main(stage_override: Optional[str] = None, use_esp32_indoor: bool = True) -> int:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _request_shutdown)
        except ValueError:
            # Handlers can only be installed from the main thread. Running the
            # loop from a thread (a test harness, an embedded runner) is still
            # valid: shutdown then comes from _request_shutdown being called
            # directly, and the `finally` block still de-energises everything.
            pass

    ensure_schema()

    relays = Relays(mode=relay_mode_from_env())
    sensors = Sensors(
        use_esp32_indoor=use_esp32_indoor, indoor_sensors=indoor_sensors_from_env()
    )
    state = ControllerState()
    tuning = control_tuning_from_env()
    config = ControllerConfig(
        min_on_seconds=tuning.min_on_seconds,
        min_off_seconds=tuning.min_off_seconds,
        min_changeover_seconds=tuning.min_changeover_seconds,
        stale_after_seconds=tuning.stale_after_seconds,
    )

    relays.setup()
    sensors.load_calibration()
    sensors.load_drift_baseline()
    sensors.init_indoor_dht22()
    # The outdoor sensor initialises itself inside its own thread, so a DHT22
    # that takes seconds to come up does not delay the first control decision.
    sensors.start_outdoor_reader()

    print("📡 Indoor sensor: ESP32 via the web API" if use_esp32_indoor
          else "🔌 Indoor sensor: local DHT22")
    print("🔀 Relays: remote node polling for the desired state"
          if relays.mode == RELAY_MODE_REMOTE else "🔌 Relays: local GPIO")
    if sensors.indoor_sensors.witness:
        print(f"👁️  Cross-check: '{sensors.indoor_sensors.primary}' controls, "
              f"'{sensors.indoor_sensors.witness}' only watches it for drift")
    print(f"⚙️  Min on {config.min_on_seconds:.0f}s, min off {config.min_off_seconds:.0f}s, "
          f"changeover {config.min_changeover_seconds:.0f}s, "
          f"failsafe at {config.stale_after_seconds:.0f}s; "
          f"deadband derived from each stage's band")

    stage: Optional[str] = None
    grow_id: Optional[int] = None
    stage_band = StageBand()
    stuck_guard = humidifier_rescue_guard()
    last_stage_check = -STAGE_CHECK_INTERVAL_SECONDS
    # Start the clock now rather than in the past. Firing on the first iteration
    # would store a row summarising a single reading, so every restart would add
    # a sample that claims to represent five minutes of data from one sample.
    last_db_save = monotonic()
    last_sensor_reinit = 0.0
    unknown_stage_warned: Optional[str] = None
    #: Which uncontrolled mode the loop is in, or None while it is in charge.
    #: A boolean until monitoring existed, when there was only one way to be
    #: stood down; it has to name *which* now, so that switching straight from
    #: one to the other is noticed instead of looking like no change at all.
    uncontrolled_mode = None
    last_settings_check = -SETTINGS_CHECK_INTERVAL_SECONDS
    # Its own clock, not the settings one: the paused branch `continue`s before
    # that gets updated, so sharing it would re-read the database every cycle
    # for exactly as long as the pause lasts.
    last_pause_check = -SETTINGS_CHECK_INTERVAL_SECONDS
    quiet_windows: Dict[str, Any] = {}
    quiet_reported: FrozenSet[str] = frozenset()
    # Every reading feeds this; one row per DB_SAVE_INTERVAL_SECONDS summarises
    # the whole interval instead of whichever reading happened to land on it.
    accumulator = SampleAccumulator()

    try:
        while not _shutdown:
            loop_started = monotonic()

            try:
                # Checked first and every cycle, before anything else: a pause
                # needs neither an active grow, nor a valid stage, nor a usable
                # reading. Those are preconditions for *deciding*, and while
                # paused nothing decides.
                # The mirror is restored on the same interval the other
                # settings are read on, and before the pause is checked: a
                # Redis that came back empty must not get a cycle of automatic
                # control in before the database is consulted.
                if loop_started - last_pause_check >= SETTINGS_CHECK_INTERVAL_SECONDS:
                    last_pause_check = loop_started
                    restore_pause_mirror()

                control_mode, mode_since = read_control_mode()
                if control_mode in UNCONTROLLED_MODES:
                    if uncontrolled_mode != control_mode:
                        uncontrolled_mode = control_mode
                        relays.log_events = False
                        started = datetime.fromtimestamp(
                            mode_since or int(loop_started), ARGENTINA_TZ)
                        if control_mode == MODE_MONITOR:
                            print(f"◍ Monitoreo desde {started.strftime('%H:%M:%S')} — "
                                  "el lazo no decide y nada se enciende, pero el "
                                  "histórico se sigue guardando. Sigue así hasta "
                                  "que alguien lo saque.")
                        else:
                            print(f"⏸️ En pausa desde {started.strftime('%H:%M:%S')} — "
                                  "el lazo no decide, los relés siguen lo que se pida "
                                  "a mano y no se guarda nada. Sigue así hasta que "
                                  "alguien la saque.")
                        # Whatever was half-accumulated was accumulated under a
                        # different regime. Mixing it into the first row of this
                        # one would average a controlled tent with an idle one.
                        accumulator.reset()
                        last_db_save = loop_started

                    if control_mode == MODE_MONITOR:
                        run_monitor_cycle(relays, sensors, accumulator, loop_started)
                        last_db_save = flush_history(accumulator, last_db_save, loop_started)
                    else:
                        run_test_mode_cycle(relays, sensors, config,
                                            mode_since or int(loop_started), loop_started)
                    sleep(LOOP_INTERVAL_SECONDS)
                    continue

                if uncontrolled_mode is not None:
                    ended, uncontrolled_mode = uncontrolled_mode, None
                    relays.log_events = True
                    # The controller's state machine describes a tent it has not
                    # been driving: its timers date from before the test. Starting
                    # it fresh is what stops the first automatic decision from
                    # thinking a relay has been on for hours. The stage is re-read
                    # for the same reason.
                    state = ControllerState()
                    last_stage_check = -STAGE_CHECK_INTERVAL_SECONDS
                    accumulator.reset()
                    last_db_save = loop_started
                    print(f"✅ {'Monitoreo' if ended == MODE_MONITOR else 'Pausa'} "
                          "terminado — vuelve el control automático")

                if loop_started - last_settings_check >= SETTINGS_CHECK_INTERVAL_SECONDS:
                    last_settings_check = loop_started
                    quiet_windows = windows_from_dict(get_setting(QUIET_HOURS_SETTING, {}))

                if loop_started - last_stage_check >= STAGE_CHECK_INTERVAL_SECONDS:
                    last_stage_check = loop_started
                    active_grow = get_active_grow()
                    if not active_grow:
                        print("⚠️ No active grow. Create one before running the control loop.")
                        relays.all_off()
                        sleep(LOOP_INTERVAL_SECONDS)
                        continue

                    new_stage = stage_override or active_grow["stage"]
                    source = "override" if stage_override else f"grow '{active_grow['name']}'"

                    table = stage_band.refresh(active_grow["id"])
                    if new_stage not in table:
                        if new_stage != unknown_stage_warned:
                            unknown_stage_warned = new_stage
                            print(f"❌ Grow has an unknown stage {new_stage!r}. "
                                  f"Valid stages: {', '.join(table)}. Outputs stay off.")
                        stage = None
                        relays.all_off()
                        sleep(LOOP_INTERVAL_SECONDS)
                        continue
                    unknown_stage_warned = None

                    if new_stage != stage or active_grow["id"] != grow_id:
                        print(f"\n{'🔄 Stage changed: ' + str(stage) + ' → ' if stage else '✅ Stage: '}"
                              f"{new_stage}  (source: {source})")
                        stage, grow_id = new_stage, active_grow["id"]
                        state = ControllerState()

                if stage is None:
                    sleep(LOOP_INTERVAL_SECONDS)
                    continue

                sensors_data = sensors.read()

                if sensors_data is None:
                    # Failsafe: no usable reading, so stop driving the tent.
                    decision = decide(
                        humidity=None, target_humidity=None, now=loop_started,
                        state=state, config=config,
                    )
                    relays.all_off()
                    accumulator.push(None, quality=QUALITY_FAILSAFE,
                                     control_action=decision.action)
                    sensors.publish_status(indoor_ok=False, indoor_error="No valid indoor reading")
                    if decision.changed:
                        print(f"⚠️ No valid sensor data ({decision.reason}) — all outputs off")
                    # Re-creating the sensor objects every 3s achieves nothing and
                    # floods the log, so back off between attempts. Only the
                    # indoor one: the outdoor sensor re-initialises itself on
                    # its own thread, and it is not why the loop is in failsafe.
                    if loop_started - last_sensor_reinit >= SENSOR_REINIT_INTERVAL_SECONDS:
                        last_sensor_reinit = loop_started
                        print("🔄 Reinitialising sensors...")
                        sensors.init_indoor_dht22()
                    sleep(LOOP_INTERVAL_SECONDS)
                    continue

                temperature = float(sensors_data["temperature"])
                humidity = float(sensors_data["humidity"])

                setpoint = resolve_setpoint(stage, temperature, humidity, band=stage_band.get(stage))

                sensors_data["leaf_temperature"] = setpoint.leaf_temperature
                sensors_data["leaf_vpd"] = setpoint.leaf_vpd
                sensors_data["target_humidity"] = setpoint.target_humidity
                sensors_data["humidity_band"] = list(setpoint.humidity_band)
                sensors_data["vpd_in_range"] = setpoint.vpd_in_range
                sensors_data["stage"] = stage
                sensors_data["timestamp"] = int(_now_local().timestamp())
                sensors_data["datetime"] = _now_local().strftime("%Y-%m-%d %H:%M:%S")

                # Evaluated against the wall clock, which is what a quiet
                # window is about, and against the humidity, which is what its
                # escape is about.
                blocked = blocked_actions(quiet_windows, _now_local().time(), humidity)
                if blocked != quiet_reported:
                    for action in sorted(blocked - quiet_reported):
                        print(f"🔇 Franja de silencio: no se va a {action}")
                    for action in sorted(quiet_reported - blocked):
                        print(f"🔊 Fin de la franja de silencio para {action}")
                    quiet_reported = blocked

                decision = decide(
                    humidity=humidity,
                    target_humidity=setpoint.target_humidity,
                    now=loop_started,
                    state=state,
                    config=replace(config, deadband_pct=setpoint.deadband_pct),
                    reading_age_seconds=sensors.reading_age_seconds,
                    blocked=blocked,
                )
                sensors_data["control_action"] = decision.action
                sensors_data["control_reason"] = decision.reason
                sensors_data["quiet_hours"] = sorted(blocked)
                sensors_data["reading_age_seconds"] = round(sensors.reading_age_seconds, 1)

                if decision.changed:
                    arrow = {HUMIDIFY: "🔼 humidifying", DEHUMIDIFY: "🔽 dehumidifying", IDLE: "⏸️  idle"}
                    print(f"{arrow[decision.action]} — {humidity:.1f}% → "
                          f"{setpoint.target_humidity:.0f}% "
                          f"[band {setpoint.humidity_band[0]:.0f}-{setpoint.humidity_band[1]:.0f}%] "
                          f"({decision.reason})")

                if decision.failsafe:
                    # Stale or missing data: de-energise everything and ignore
                    # manual overrides until a fresh reading comes back.
                    relays.all_off()
                    overrides = {}
                else:
                    overrides = read_manual_overrides()
                    relays.apply(decision.action, overrides=overrides)
                    stuck_guard.observe(humidity=humidity, band_max=setpoint.humidity_band[1],
                                        humidifier_on=relays.is_on("humidity_up"), now=loop_started)
                sensors_data["manual_overrides"] = overrides

                _set_redis("sensors", json.dumps(sensors_data))
                sensors.publish_status(indoor_ok=True)

                # A reading the median filter carried through from older
                # samples is real data, but not fresh data; mark it so the
                # history can be filtered on it later.
                quality = QUALITY_OK if sensors.reading_age_seconds <= LOOP_INTERVAL_SECONDS * 3 \
                    else QUALITY_DEGRADED
                accumulator.push(sensors_data, quality=quality,
                                 control_action=decision.action)

                last_db_save = flush_history(accumulator, last_db_save, loop_started)

            except Exception as e:
                print(f"❌ Error in main loop: {e}")
                # An unexpected failure must not leave the outputs energised.
                try:
                    relays.all_off()
                except Exception:
                    pass

            elapsed = monotonic() - loop_started
            sleep(max(0.0, LOOP_INTERVAL_SECONDS - elapsed))
    finally:
        print("🔻 Turning all outputs off...")
        relays.all_off()
        relays.close()
        sensors.close()

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VPD / humidity control loop")
    parser.add_argument(
        "stage",
        nargs="?",
        choices=list(STAGES),
        help="Override the active grow's stage (optional)",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--esp32",
        dest="use_esp32",
        action="store_true",
        default=True,
        help="Read the indoor sensor from the ESP32 via the web API (default)",
    )
    source.add_argument(
        "--local",
        dest="use_esp32",
        action="store_false",
        help="Read the indoor sensor from a local DHT22 on GPIO instead",
    )
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    raise SystemExit(main(stage_override=args.stage, use_esp32_indoor=args.use_esp32))
