"""
The per-cycle work of the control loop that is not a control decision: the monitoring and
pause cycles, and the history flush.
"""

from __future__ import annotations

import json

from autocann.config import (
    MODE_MONITOR,
    ControlTuning,
)
from autocann.control.sampling import QUALITY_DEGRADED, QUALITY_FAILSAFE, QUALITY_OK, SampleAccumulator
from autocann.db import store_sensor_sample
from autocann.hardware.sensors import Sensors
from autocann.runtime.clock import _now_local
from autocann.runtime.outputs import Relays, read_test_mode_outputs
from autocann.runtime.redis import _set_redis

LOOP_INTERVAL_SECONDS = 3.0


#: How often a sample is persisted to SQLite.
DB_SAVE_INTERVAL_SECONDS = 300.0
#: How often the active grow / stage is re-read from the database.
STAGE_CHECK_INTERVAL_SECONDS = 60.0
#: How often the operator's settings are re-read. Short enough that editing a
#: quiet window on the dashboard takes effect while you are still looking at it:
#: calibration is read once at startup, which would be the wrong trade here.
SETTINGS_CHECK_INTERVAL_SECONDS = 30.0
#: Settings key holding the per-output quiet windows.
QUIET_HOURS_SETTING = "quiet_hours"
#: Minimum gap between attempts to re-create failed sensor objects.
SENSOR_REINIT_INTERVAL_SECONDS = 30.0


def run_monitor_cycle(relays: "Relays", sensors: "Sensors",
                      accumulator: SampleAccumulator, now: float) -> None:
    """
    One cycle with the controller switched off and the history still running.

    The other half of `run_test_mode_cycle`, and the difference is the whole
    point of the mode: the readings go into the accumulator, so the row that
    gets written every few minutes is written in monitoring exactly as it is in
    control. What the room does on its own is the baseline somebody setting a
    tent up actually wants, and a pause threw it away.

    Nothing switches. The relays are driven to off every cycle rather than left
    alone, because the desired state has to keep being published either way:
    the remote node de-energises 60 s after the last successful poll, and a
    loop that went quiet here would have the node reaching its own failsafe
    instead of being told. Told is better - it is the same outcome arrived at
    on purpose, and it is what the dashboard then shows.

    What is *not* recorded is any of the control context: no stage, no target
    humidity, no action. There is no controller, so there is no target, and a
    row carrying one would describe a decision nobody made. That is also what
    makes a monitoring stretch recognisable in the history afterwards without
    inventing a flag for it: real readings, no decision.
    """
    relays.all_off()

    reading = sensors.read()
    payload = dict(reading or {})
    payload.update(
        {
            "mode": MODE_MONITOR,
            "timestamp": int(_now_local().timestamp()),
            "datetime": _now_local().strftime("%Y-%m-%d %H:%M:%S"),
            "stage": None,
            "target_humidity": None,
            "vpd_in_range": None,
            "control_action": None,
            "control_reason": "monitoreo: el lazo no decide, pero se guarda",
            "manual_overrides": {},
        }
    )
    _set_redis("sensors", json.dumps(payload))

    if reading is None:
        # Counts towards the interval's quality even though it carries no
        # numbers, exactly as the controlled path does: a stretch where half
        # the reads failed must not be stored as if it were clean.
        accumulator.push(None, quality=QUALITY_FAILSAFE)
        sensors.publish_status(indoor_ok=False, indoor_error="Sin lectura válida (monitoreo)")
        return

    quality = QUALITY_OK if sensors.reading_age_seconds <= LOOP_INTERVAL_SECONDS * 3 \
        else QUALITY_DEGRADED
    accumulator.push(payload, quality=quality)
    sensors.publish_status(indoor_ok=True)


def run_test_mode_cycle(relays: "Relays", sensors: "Sensors", tuning: ControlTuning,
                        since: int, now: float) -> None:
    """
    One cycle with the controller switched off.

    The relays follow what was asked by hand. The sensors are still read and
    published, so the dashboard does not go dark while someone is working on the
    tent — but nothing is accumulated and nothing is stored, which is the point.

    The desired state is still published every cycle. The remote node
    de-energises after 60 s without a poll, so a loop that went quiet while
    paused would drop the relays once a minute and make the bench work useless.
    """
    held = relays.apply_manual(read_test_mode_outputs(), tuning=tuning, now=now)

    reading = sensors.read()
    payload = dict(reading or {})
    payload.update(
        {
            "mode": "test",
            "test_mode_since": since,
            "timestamp": int(_now_local().timestamp()),
            "datetime": _now_local().strftime("%Y-%m-%d %H:%M:%S"),
            # There is no target and no decision in test mode. Publishing the
            # last ones would leave the dashboard showing a controller that
            # looks like it is still working.
            "stage": None,
            "target_humidity": None,
            "vpd_in_range": None,
            "control_action": None,
            "control_reason": "en pausa: el lazo no decide",
            "held_outputs": held,
        }
    )
    _set_redis("sensors", json.dumps(payload))

    if reading is None:
        sensors.publish_status(indoor_ok=False, indoor_error="Sin lectura válida (en pausa)")
    else:
        sensors.publish_status(indoor_ok=True)


def flush_history(accumulator: SampleAccumulator, last_db_save: float,
                  now: float) -> float:
    """
    Write the interval's row if the interval is up. Returns the new deadline.

    Extracted when monitoring arrived, because both regimes store history and
    two copies of this is how one of them quietly starts summarising its
    interval differently from the other.
    """
    if now - last_db_save < DB_SAVE_INTERVAL_SECONDS:
        return last_db_save

    summary = accumulator.build()
    if summary is None:
        # Nothing usable in the whole interval. Leaving a hole is the honest
        # record; the charts draw it as a break.
        accumulator.reset()
        return now
    if not store_sensor_sample(summary):
        # Could not store - most often because there is no active grow to hang
        # the row on. Keep accumulating rather than dropping the interval.
        return last_db_save

    print(f"💾 Muestra guardada: {summary['sample_n']} lecturas, "
          f"T {summary.get('temperature_min')}–{summary.get('temperature_max')}°C, "
          f"HR {summary.get('humidity_min')}–{summary.get('humidity_max')}%")
    accumulator.reset()
    return now
