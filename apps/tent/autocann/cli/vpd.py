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
import threading
from dataclasses import dataclass, replace
from datetime import datetime
from time import monotonic, sleep
from typing import Any, Dict, FrozenSet, Optional, Tuple

from autocann.config import (
    DESIRED_OUTPUTS_KEY,
    INDOOR_SENSOR_MAX_AGE_SECONDS,
    MODE_ACTIVE,
    MODE_MONITOR,
    MODE_PAUSED,
    RELAY_MODE_GPIO,
    RELAY_MODE_REMOTE,
    TEST_MODE_KEY,
    TEST_MODE_OUTPUTS_KEY,
    TEST_MODE_SETTING,
    UNCONTROLLED_MODES,
    ControlTuning,
    IndoorSensors,
    control_tuning_from_env,
    indoor_sensors_from_env,
    redis_config_from_env,
    relay_mode_from_env,
)
from autocann.control.drift import DriftMonitor, DriftReport
from autocann.control.humidity import (
    DEHUMIDIFY,
    HUMIDIFY,
    IDLE,
    ControllerConfig,
    ControllerState,
    MedianFilter,
    decide,
)
from autocann.control.plausible import plausible_reading
from autocann.control.quiet import blocked_actions, windows_from_dict
from autocann.control.reading_age import parse_esp32_sample
from autocann.control.sampling import QUALITY_DEGRADED, QUALITY_FAILSAFE, QUALITY_OK, Calibration, SampleAccumulator
from autocann.control.vpd_math import (
    LEAF_TEMP_OFFSET_C,
    STAGES,
    calculate_leaf_vpd,
    calculate_vpd,
    humidity_range_bounds_for_stage,
    humidity_range_for_stage,
    vpd_is_in_range,
)
from autocann.db import (
    ensure_schema,
    get_active_grow,
    get_calibration,
    get_setting,
    store_control_event,
    store_sensor_sample,
)
from autocann.hardware.outputs import (
    get_outputs,
    manual_override_key,
    parse_bool_flag,
    pin_sources,
)
from autocann.redis_pool import pooled_client
from autocann.time import ARGENTINA_TZ

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
#: ESP32 data older than this is ignored. Defined in `autocann.config` because
#: the dashboard marks a reading stale with the same number, and a dashboard
#: calling a reading fresh that the loop is refusing to act on is worse than
#: either answer on its own.
ESP32_MAX_AGE_SECONDS = INDOOR_SENSOR_MAX_AGE_SECONDS
#: Expiry on the Redis keys the dashboard reads, so a dead loop stops looking alive.
REDIS_KEY_TTL_SECONDS = 600
#: Minimum gap between attempts to re-create failed sensor objects.
SENSOR_REINIT_INTERVAL_SECONDS = 30.0
#: Redis key holding the learned drift baseline. Written without a TTL on
#: purpose: a sensor drifts over weeks, so a baseline that expired with the
#: other dashboard keys would be re-learned from the drifted sensor and the
#: detector would never fire.
DRIFT_BASELINE_KEY = "indoor_drift_baseline"

#: Recurring warnings print at most this often, so a day-long fault does not
#: write 28,800 identical lines to the log.
WARN_INTERVAL_SECONDS = 60.0

#: DHT22 needs ~2s between reads, so each attempt is expensive. Keep the count
#: low: the median filter and the failsafe handle intermittent failures, and a
#: long blocking retry would leave the relays unsupervised.
DHT_READ_ATTEMPTS = 2
DHT_RETRY_DELAY_SECONDS = 2.0

#: DHT22 GPIO pins (BCM), used only in local-sensor mode.
DHT22_INDOOR_PIN = 4
DHT22_OUTDOOR_PIN = 13

#: How often the outdoor sensor is read, on its own thread.
#:
#: The outdoor reading feeds a chart and nothing else — no decision in this
#: program depends on it — so it is read at the cadence a chart needs rather
#: than at the cadence of the control loop. It used to be read inline, which
#: meant a dead DHT22 outside stalled the critical loop for about two seconds
#: of retries and sleeps on every single cycle, for a number nobody decides
#: anything with.
OUTDOOR_READ_INTERVAL_SECONDS = 60.0

#: A cached outdoor reading older than this is not served at all.
#:
#: The cache exists so the loop never blocks, not so the dashboard can be told
#: something old as if it were current. Five of the reader's own cycles: long
#: enough that a few failed reads in a row do not blink the number out, short
#: enough that a sensor that died stops having an opinion about the weather.
OUTDOOR_MAX_AGE_SECONDS = 5 * OUTDOOR_READ_INTERVAL_SECONDS

_redis_cfg = redis_config_from_env()
#: Bounded pool, and a socket with a timeout so that the `try/except` around
#: every read and write below can actually fire. See autocann/redis_pool.py.
redis_client = pooled_client(_redis_cfg)


def _now_local() -> datetime:
    return datetime.now(ARGENTINA_TZ)


def _offset_dict(offset) -> Optional[Dict[str, float]]:
    """A drift Offset as plain JSON, or None when there is not one yet."""
    if offset is None:
        return None
    return {"temperature": round(offset.temperature, 2), "humidity": round(offset.humidity, 2)}


def _set_redis(key: str, value: str, ttl: Optional[int] = REDIS_KEY_TTL_SECONDS) -> None:
    """Best-effort Redis write. Redis being down must not stop the control loop."""
    try:
        redis_client.set(key, value, ex=ttl)
    except Exception as e:
        print(f"⚠️ Redis write failed for '{key}': {e}")


# ---------------------------------------------------------------------------
# Relays
# ---------------------------------------------------------------------------


class Relays:
    """
    Owns the humidity output pins.

    This process is the only GPIO owner: gpiozero raises GPIOPinInUse if a
    second process claims the same pin, and a pin released by `close()` reverts
    to its default state, which silently drops the relay.
    """

    def __init__(self, mode: str = RELAY_MODE_GPIO) -> None:
        #: RELAY_MODE_GPIO drives local pins; RELAY_MODE_REMOTE only publishes
        #: the desired state for a node that polls for it.
        self.mode = mode
        self._specs = {o["name"]: o for o in get_outputs()}
        self._devices: Dict[str, Any] = {name: None for name in self._specs}
        self._state: Dict[str, bool] = {name: False for name in self._specs}
        #: Whether transitions reach the event log at all. Test mode turns it
        #: off for the whole mode rather than at each call site: the paths that
        #: switch a relay without being asked to — the shutdown handler, the
        #: catch-all in the loop — are exactly the ones that would forget, and
        #: a bench session leaving one row per shutdown in the history is the
        #: promise of "nothing is stored" quietly broken.
        self.log_events = True
        #: Monotonic time of each output's last real transition. Unset means it
        #: has not switched in this process's lifetime, so nothing is waiting.
        #: Used only by the manual path: the automatic one gets its minimum
        #: on/off times from the controller's own state machine.
        self._changed_at: Dict[str, float] = {}

    def setup(self) -> None:
        if self.mode == RELAY_MODE_REMOTE:
            # The node's own GPIO, which is the number the dashboard shows too:
            # this board's header is not in the circuit at all.
            sources = pin_sources(self.mode)
            for name, spec in self._specs.items():
                source = sources.get(name) or {}
                print(f"📡 {spec['label']} → {source.get('node') or 'nodo remoto'} "
                      f"GPIO {source.get('number')}")
            self.all_off(log_event=False)
            self.publish_outputs()
            return

        import gpiozero  # imported lazily so this module stays importable off-Pi

        for name, spec in self._specs.items():
            self._devices[name] = gpiozero.OutputDevice(
                int(spec["pin_bcm"]),
                active_high=bool(spec.get("active_high", True)),
                initial_value=False,
            )
            print(f"🔌 {spec['label']} → BCM {spec['pin_bcm']}")
        self.all_off(log_event=False)
        self.publish_outputs()

    def apply(self, action: str, *, overrides: Optional[Dict[str, bool]] = None,
              log_event: bool = True) -> None:
        """
        Drive every output to the state implied by `action`, with `overrides`
        taking precedence.

        The desired state is resolved first and written once. Applying the
        automatic action and then the overrides on top would toggle an overridden
        relay off and on again on every single iteration.
        """
        desired = {name: False for name in self._specs}
        desired["humidity_up"] = action == HUMIDIFY
        desired["humidity_down"] = action == DEHUMIDIFY

        for name, forced in (overrides or {}).items():
            if name in desired:
                desired[name] = forced

        for name, on in desired.items():
            self.set_output(name, on, log_event=log_event)
        self.publish_state()

    def all_off(self, *, log_event: bool = True) -> None:
        """Unconditionally de-energise everything. Overrides do not apply here."""
        for name in self._specs:
            self.set_output(name, False, log_event=log_event)
        self.publish_state()

    def publish_outputs(self) -> None:
        """
        Write every output's state to Redis whether or not it just changed.

        `set_output` short-circuits when nothing changed, which is right for the
        pin and for the event log, but it means an output that has never
        switched has no key at all. The dashboard reads a missing key as
        "unknown" and draws a dash over a relay that is known to be off — which
        is most visible in test mode, where someone is looking straight at the
        cards waiting for one to move.
        """
        for name, spec in self._specs.items():
            redis_key = spec.get("redis_key")
            if redis_key:
                _set_redis(redis_key, "true" if self._state[name] else "false")

    def publish_state(self) -> None:
        """
        Publish the whole desired state, timestamped, on every cycle.

        Deliberately uncached: `set_output` short-circuits when nothing changed,
        which is right for logging and for a local pin, but a remote node needs
        to hear from the loop on every cycle. Two reasons:

        - the poll is also the keepalive, so a silent cycle reads as "the Pi is
          gone" and the node de-energises;
        - if the node ever drops the relays on its own, the loop's cached state
          would still say "on" and it would never re-send the command. Sending
          the full state every time makes that divergence impossible.
        """
        payload = {
            "outputs": dict(self._state),
            "ts": int(_now_local().timestamp()),
        }
        _set_redis(DESIRED_OUTPUTS_KEY, json.dumps(payload))

    def set_output(self, name: str, on: bool, *, log_event: bool = True,
                   now: Optional[float] = None) -> None:
        """
        `now` is the monotonic reading to stamp the transition with. It is a
        parameter rather than read here so that a caller working from one clock
        reading — the loop does, and so do the tests — records the same instant
        it is reasoning about.
        """
        spec = self._specs.get(name)
        if spec is None or self._state.get(name) == on:
            return

        if self.mode == RELAY_MODE_GPIO:
            device = self._devices.get(name)
            try:
                if device is not None:
                    if on:
                        device.on()
                    else:
                        device.off()
            except Exception as e:
                print(f"❌ Failed to switch {name}: {e}")
                return

        self._state[name] = on
        self._changed_at[name] = monotonic() if now is None else now
        redis_key = spec.get("redis_key")
        if redis_key:
            _set_redis(redis_key, "true" if on else "false")
        if log_event and self.log_events:
            # Only transitions are recorded. The old loop logged an event on
            # every iteration, which added ~29k rows per day.
            store_control_event(name, "on" if on else "off")

    def hold_remaining(self, name: str, tuning: ControlTuning, now: float) -> float:
        """
        Seconds this output must stay as it is before it may switch again.

        An output that is on owes its minimum *on* time; one that is off owes
        its minimum *off* time.
        """
        changed_at = self._changed_at.get(name)
        if changed_at is None:
            return 0.0
        minimum = tuning.min_on_seconds if self._state.get(name) else tuning.min_off_seconds
        return max(0.0, minimum - (now - changed_at))

    def apply_manual(self, desired: Dict[str, bool], *, tuning: ControlTuning,
                     now: float) -> Dict[str, float]:
        """
        Drive the outputs to exactly what was asked, with nothing deciding
        behind them. Returns the outputs held back, with the seconds left.

        Two things this does not do, both deliberate:

        - **It logs no control events.** Test mode writes nothing to the
          database; a bench session toggling relays by hand is not history.
        - **It does not waive the minimum on/off times.** Those protect the
          equipment, and the equipment does not care whether the command came
          from a person or from the controller. A command that has to wait is
          *reported*, not dropped, so the dashboard can say why a relay did not
          move instead of looking broken.
        """
        held: Dict[str, float] = {}
        for name in self._specs:
            want = bool(desired.get(name, False))
            if want != self._state.get(name, False):
                remaining = self.hold_remaining(name, tuning, now)
                if remaining > 0:
                    held[name] = round(remaining, 1)
                    continue
            self.set_output(name, want, log_event=False, now=now)
        self.publish_state()
        return held

    def close(self) -> None:
        for device in self._devices.values():
            try:
                if device is not None:
                    device.off()
                    device.close()
            except Exception:
                pass


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


def read_test_mode_since() -> Optional[int]:
    """
    When the pause started, or None when the loop is in control.

    Read from Redis every cycle because it is cheap. Redis is a mirror, not the
    truth - `restore_pause_mirror` puts it back from the database - so the one
    thing this cannot do is treat a missing key as "resume". It still treats an
    *unreachable* Redis as resume, which is a different case: the loop cannot
    be driven by hand without it either, so carrying on controlling the tent is
    the useful answer rather than standing down with nobody able to intervene.
    """
    mode, since = read_control_mode()
    return since if mode in UNCONTROLLED_MODES else None


def parse_mode_mirror(raw) -> Tuple[str, Optional[int]]:
    """
    The Redis mirror, as (mode, since).

    Two formats, on purpose. It now holds `{"mode": ..., "since": ...}`, but a
    Redis that survived the deploy which added monitoring still holds a bare
    timestamp from before - and the one reading of that which is safe is the
    one it always had: a pause. Guessing "monitor" would hand the relays to a
    controller somebody had deliberately stopped.
    """
    if not raw:
        return MODE_ACTIVE, None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")

    text = str(raw).strip()
    try:
        return MODE_PAUSED, int(text)
    except (TypeError, ValueError):
        pass

    try:
        stored = json.loads(text)
    except (TypeError, ValueError):
        return MODE_ACTIVE, None
    if not isinstance(stored, dict):
        return MODE_ACTIVE, None

    mode = stored.get("mode")
    if mode not in UNCONTROLLED_MODES:
        # A mode this build does not know is not a reason to start driving a
        # tent somebody stopped. Stand down and keep the history.
        mode = MODE_MONITOR if mode else MODE_PAUSED
    try:
        since = int(stored.get("since"))
    except (TypeError, ValueError):
        since = None
    return mode, since


def read_control_mode() -> Tuple[str, Optional[int]]:
    """
    Which of the three states the tent is in, and since when.

    Read from Redis every cycle because it is cheap. Redis is a mirror, not the
    truth - `restore_pause_mirror` puts it back from the database - so the one
    thing this cannot do is treat a missing key as "resume". It still treats an
    *unreachable* Redis as resume, which is a different case: the loop cannot
    be driven by hand without it either, so carrying on controlling the tent is
    the useful answer rather than standing down with nobody able to intervene.
    """
    try:
        raw = redis_client.get(TEST_MODE_KEY)
    except Exception:
        return MODE_ACTIVE, None
    return parse_mode_mirror(raw)


def stored_control_mode() -> Tuple[str, Optional[int]]:
    """
    What the database says, which is the truth.

    A record written before monitoring existed carries no `mode`, and every one
    of those meant a pause.
    """
    stored = get_setting(TEST_MODE_SETTING, None)
    if not isinstance(stored, dict) or not stored.get("active"):
        return MODE_ACTIVE, None

    mode = stored.get("mode")
    if mode not in UNCONTROLLED_MODES:
        mode = MODE_PAUSED
    try:
        since = int(stored.get("since"))
    except (TypeError, ValueError):
        since = None
    return mode, since


def restore_pause_mirror() -> None:
    """
    Put the Redis mirror back when the database says the tent is not controlled.

    Redis is memory. Restarting it, or the Raspberry, used to mean a pause
    silently ended and the relays started moving again at a moment nobody
    chose. Checked on the settings interval rather than every cycle: it only
    has to beat somebody walking back to the tent.
    """
    mode, since = stored_control_mode()
    if mode not in UNCONTROLLED_MODES:
        return
    try:
        if redis_client.get(TEST_MODE_KEY):
            return
        since = int(since or int(_now_local().timestamp()))
        redis_client.set(TEST_MODE_KEY, json.dumps({"mode": mode, "since": since}))
        label = "Monitoreo" if mode == MODE_MONITOR else "Pausa"
        print(f"⏸️ {label} restaurado desde la base: Redis no lo tenía")
    except Exception:
        pass


def read_test_mode_outputs() -> Dict[str, bool]:
    """What the operator last asked each output to do in test mode."""
    try:
        raw = redis_client.get(TEST_MODE_OUTPUTS_KEY)
        requested = json.loads(raw) if raw else {}
    except Exception:
        return {}
    if not isinstance(requested, dict):
        return {}
    return {spec["name"]: bool(requested.get(spec["name"], False)) for spec in get_outputs()}


def read_manual_overrides() -> Dict[str, bool]:
    """
    Manual overrides published by the web API, keyed by output name.

    The keys carry a Redis TTL, so an override the operator forgets about lapses
    back to automatic control on its own.
    """
    overrides: Dict[str, bool] = {}
    for spec in get_outputs():
        name = spec["name"]
        try:
            raw = redis_client.get(manual_override_key(name))
        except Exception:
            continue
        value = parse_bool_flag(raw)
        if value is not None:
            overrides[name] = value
    return overrides


# ---------------------------------------------------------------------------
# Sensors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OutdoorReading:
    """
    One outdoor sample and the monotonic instant it was taken at.

    The timestamp travels with the values on purpose: a cached reading that
    cannot say how old it is would be served forever, and a chart drawing
    yesterday's weather as today's is the failure this whole cache has to avoid.
    """

    temperature: float
    humidity: float
    at: float


class Sensors:
    """Indoor (ESP32 via Redis, or local DHT22) and outdoor (local DHT22) readings."""

    def __init__(
        self,
        use_esp32_indoor: bool = True,
        indoor_sensors: Optional[IndoorSensors] = None,
    ) -> None:
        self.use_esp32_indoor = use_esp32_indoor
        #: Which Redis keys hold the indoor readings. Defaults to the legacy
        #: single-key installation, so nothing changes until a witness is
        #: configured.
        self.indoor_sensors = indoor_sensors or IndoorSensors()
        self.drift = DriftMonitor()
        #: Last verdict from the drift monitor, published for the dashboard.
        self.drift_report: Optional[DriftReport] = None
        self._dht_in: Any = None
        self._dht_out: Any = None
        self.indoor_temp = MedianFilter(5)
        self.indoor_humidity = MedianFilter(5)
        self.indoor_source: Optional[str] = None
        #: Per-sensor corrections, loaded once at startup.
        self.indoor_calibration = Calibration()
        self.outdoor_calibration = Calibration()
        #: Monotonic time of the last successful raw indoor read. The median
        #: filter keeps returning its last value when a read fails, so without
        #: this the loop would act on old samples forever and the failsafe would
        #: never trip.
        self.last_reading_at: Optional[float] = None
        self._payload_ages: Dict[str, float] = {}
        self._last_warned: Dict[str, float] = {}
        #: Last outdoor sample, written by the reader thread and read by the
        #: control loop, so it is guarded. Starts empty: no reading is the
        #: honest state until one arrives.
        self._outdoor: Optional[OutdoorReading] = None
        self._outdoor_error: Optional[str] = "Sin lectura exterior todavía"
        self._outdoor_lock = threading.Lock()
        self._outdoor_stop = threading.Event()
        self._outdoor_thread: Optional[threading.Thread] = None

    def _warn_throttled(self, key: str, message: str) -> None:
        """Print a recurring warning at most once per WARN_INTERVAL_SECONDS."""
        now = monotonic()
        if now - self._last_warned.get(key, -WARN_INTERVAL_SECONDS) >= WARN_INTERVAL_SECONDS:
            self._last_warned[key] = now
            print(message)

    # -- local DHT22 -------------------------------------------------------

    def _board_pin(self, gpio_num: int):
        import board

        pin = getattr(board, f"D{gpio_num}", None)
        if pin is None:
            raise ValueError(f"GPIO {gpio_num} has no board.D{gpio_num} mapping")
        return pin

    def load_calibration(self) -> None:
        """
        Read the stored offsets once.

        Done at startup rather than per reading: a database round trip every
        three seconds buys nothing, and an operator changing a calibration can
        restart the loop.
        """
        self.indoor_calibration = get_calibration(
            "esp32_indoor" if self.use_esp32_indoor else "dht22_indoor")
        self.outdoor_calibration = get_calibration("dht22_outdoor")
        for name, cal in (("interior", self.indoor_calibration),
                          ("exterior", self.outdoor_calibration)):
            if not cal.is_identity:
                print(f"🎚️  Calibración {name}: {cal.temperature_offset:+.1f}°C, "
                      f"{cal.humidity_offset:+.1f}%")

    def _init_dht22(self, attr: str, gpio_num: int, label: str) -> None:
        """(Re)create one local DHT22 object."""
        try:
            import adafruit_dht
        except Exception as e:
            self._warn_throttled(
                f"dht_import_{label}",
                f"⚠️ adafruit_dht unavailable, skipping local DHT22 {label}: {e}")
            return

        existing = getattr(self, attr)
        if existing is not None:
            try:
                existing.exit()
            except Exception:
                pass
        try:
            setattr(self, attr, adafruit_dht.DHT22(self._board_pin(gpio_num), use_pulseio=False))
            print(f"✅ DHT22 {label} initialised on GPIO {gpio_num}")
        except Exception as e:
            setattr(self, attr, None)
            print(f"❌ DHT22 {label} init failed on GPIO {gpio_num}: {e}")

    def init_indoor_dht22(self) -> None:
        """
        (Re)initialise the local indoor DHT22, when there is one.

        Split from the outdoor sensor deliberately. The control loop calls this
        from its failsafe branch, while the outdoor object belongs to the reader
        thread; re-creating both from here would have two threads closing and
        replacing the same device under each other.
        """
        if self.use_esp32_indoor:
            return
        self._init_dht22("_dht_in", DHT22_INDOOR_PIN, "indoor")

    def init_outdoor_dht22(self) -> None:
        """
        (Re)initialise the outdoor DHT22.

        Only the reader thread calls this, which is what makes `_dht_out` safe
        to touch without a lock.
        """
        self._init_dht22("_dht_out", DHT22_OUTDOOR_PIN, "outdoor")

    def _read_dht22(self, sensor, name: str) -> Tuple[Optional[float], Optional[float]]:
        if sensor is None:
            return None, None

        for attempt in range(DHT_READ_ATTEMPTS):
            try:
                temperature = sensor.temperature
                humidity = sensor.humidity
                if temperature is not None and humidity is not None and plausible_reading(temperature, humidity):
                    return float(temperature), float(humidity)
            except RuntimeError:
                pass  # a failed checksum is normal for a DHT22
            except Exception as e:
                self._warn_throttled(f"dht_{name}", f"⚠️ DHT22 {name} error: {e}")
            if attempt < DHT_READ_ATTEMPTS - 1:
                sleep(DHT_RETRY_DELAY_SECONDS)

        return None, None

    # -- outdoor, off the critical path ------------------------------------

    def refresh_outdoor(self) -> Optional[OutdoorReading]:
        """
        Take one outdoor reading, blocking, and cache it.

        Called from the reader thread. Exposed because it is also the whole of
        the behaviour worth testing: a test calls it once and then reads the
        cache, with no thread involved.

        A failed read does **not** clear the cache. The last reading stays
        valid for as long as it is fresh enough — a DHT22 that misses two
        checksums in a row has not changed the weather — and ages out on its
        own after that, which is what keeps a stale value from being served as
        a current one.
        """
        temperature, humidity = self._read_dht22(self._dht_out, "outdoor")
        with self._outdoor_lock:
            if temperature is None or humidity is None:
                self._outdoor_error = (
                    "DHT22 exterior sin inicializar" if self._dht_out is None
                    else "El DHT22 exterior no contestó")
                return self._outdoor
            self._outdoor = OutdoorReading(
                float(temperature), float(humidity), monotonic())
            self._outdoor_error = None
            return self._outdoor

    def outdoor_reading(self) -> Tuple[Optional[float], Optional[float]]:
        """
        The cached outdoor reading, or `(None, None)` when there is no fresh one.

        Never blocks and never raises: this is what the control loop calls.
        """
        with self._outdoor_lock:
            cached = self._outdoor
        if cached is None or monotonic() - cached.at > OUTDOOR_MAX_AGE_SECONDS:
            return None, None
        return cached.temperature, cached.humidity

    def outdoor_status(self) -> Dict[str, Any]:
        """`{ok, error}` for the outdoor sensor, judged on its actual reading."""
        temperature, _ = self.outdoor_reading()
        if temperature is not None:
            return {"ok": True, "error": None}
        with self._outdoor_lock:
            cached, error = self._outdoor, self._outdoor_error
        if cached is not None and error is None:
            # It read fine at some point and then went quiet long enough for the
            # cache to expire, which no single failed read reports.
            error = "La última lectura exterior quedó vieja"
        return {"ok": False, "error": error or "Sin lectura exterior"}

    def _outdoor_loop(self) -> None:
        """Read the outdoor sensor forever, slowly, on its own thread."""
        while not self._outdoor_stop.is_set():
            try:
                if self._dht_out is None:
                    self.init_outdoor_dht22()
                self.refresh_outdoor()
            except Exception as e:
                # This thread dying would freeze the outdoor reading at
                # whatever it last was until the cache expired, and then show
                # nothing, with no explanation anywhere.
                self._warn_throttled("outdoor_thread", f"⚠️ Lector exterior: {e}")
            self._outdoor_stop.wait(OUTDOOR_READ_INTERVAL_SECONDS)

    def start_outdoor_reader(self) -> None:
        """Start the background reader, unless it is already running."""
        if self._outdoor_thread is not None and self._outdoor_thread.is_alive():
            return
        self._outdoor_stop.clear()
        # A daemon thread: the loop's shutdown path must not wait out a DHT22
        # read to de-energise the relays.
        self._outdoor_thread = threading.Thread(
            target=self._outdoor_loop, name="outdoor-dht22", daemon=True)
        self._outdoor_thread.start()
        print(f"🌤️  Sensor exterior: leído aparte cada "
              f"{OUTDOOR_READ_INTERVAL_SECONDS:.0f}s, nunca en el lazo")

    def stop_outdoor_reader(self) -> None:
        self._outdoor_stop.set()
        thread = self._outdoor_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    # -- ESP32 over Redis --------------------------------------------------

    def _read_esp32_key(self, key: str, label: str) -> Tuple[Optional[float], Optional[float]]:
        self._payload_ages.pop(key, None)
        try:
            raw = redis_client.get(key)
            if raw is None:
                return None, None
            sample = parse_esp32_sample(raw, _now_local().timestamp())
            if sample.age > ESP32_MAX_AGE_SECONDS:
                self._warn_throttled(
                    f"esp32_stale_{key}",
                    f"⚠️ ESP32 {label} data is stale: '{key}' is {sample.age}s old "
                    f"(max {ESP32_MAX_AGE_SECONDS}s). A key nobody writes any more "
                    f"looks exactly like a dead sensor, so the key is named here.",
                )
                return None, None

            # An impossible value is a failed read: the median filter keeps the last good one.
            if None in (sample.temperature, sample.humidity) or not plausible_reading(sample.temperature, sample.humidity):
                return None, None
            self._payload_ages[key] = sample.data_age
            return float(sample.temperature), float(sample.humidity)
        except Exception as e:
            print(f"⚠️ Error reading ESP32 {label} data: {e}")
            return None, None

    def _read_esp32_indoor(self) -> Tuple[Optional[float], Optional[float]]:
        """The reading the control decision is made on."""
        return self._read_esp32_key(self.indoor_sensors.primary_key, "indoor")

    def _read_witness(self) -> Tuple[Optional[float], Optional[float]]:
        """
        The cross-check reading. Never used for control.

        A failure here is not a control failure: it must not touch
        `last_reading_at`, or a dead witness would hold the failsafe open on a
        sensor the loop does not even act on.
        """
        key = self.indoor_sensors.witness_key
        if key is None:
            return None, None
        return self._read_esp32_key(key, "witness")

    # -- drift -------------------------------------------------------------

    def load_drift_baseline(self) -> None:
        """Adopt the persisted baseline, if there is one."""
        try:
            raw = redis_client.get(DRIFT_BASELINE_KEY)
            stored = json.loads(raw) if raw else None
        except Exception as e:
            # Redis down, or a corrupt value. Neither is a reason to refuse to
            # start: the monitor simply learns a new baseline.
            print(f"⚠️ Could not read the drift baseline: {e}")
            return
        if self.drift.load(stored):
            baseline = self.drift.baseline
            print(f"📐 Drift baseline: {baseline.temperature:+.2f}°C, "
                  f"{baseline.humidity:+.2f}% (witness - primary)")

    def _record_drift(self, primary, witness) -> None:
        """Feed the monitor, persist a newly learned baseline, warn on faults."""
        had_baseline = self.drift.baseline is not None
        report = self.drift.update(primary=None if None in primary else primary,
                                   witness=None if None in witness else witness)
        self.drift_report = report

        if not had_baseline and self.drift.baseline is not None:
            # No TTL: see DRIFT_BASELINE_KEY.
            _set_redis(DRIFT_BASELINE_KEY, json.dumps(self.drift.to_dict()), ttl=None)
            baseline = self.drift.baseline
            print(f"📐 Drift baseline learned: {baseline.temperature:+.2f}°C, "
                  f"{baseline.humidity:+.2f}% (witness - primary)")

        for message in report.messages:
            self._warn_throttled(f"drift_{message[:32]}", f"⚠️ {message}")

    def _read_esp32_indoor_pair(self) -> Tuple[Optional[float], Optional[float]]:
        """
        Read the primary and, when configured, the witness.

        Returns the primary; the witness only feeds the drift monitor.
        """
        primary = self._read_esp32_indoor()
        if self.indoor_sensors.witness_key is None:
            return primary
        self._record_drift(primary, self._read_witness())
        return primary

    # -- public ------------------------------------------------------------

    def read(self) -> Optional[dict]:
        """
        One reading cycle. Returns None when there is no usable indoor reading,
        which the caller must treat as a failsafe condition.
        """
        temperature = humidity = None

        if self.use_esp32_indoor:
            temperature, humidity = self._read_esp32_indoor_pair()
            if temperature is not None:
                self.indoor_source = "esp32"

        if temperature is None or humidity is None:
            if self._dht_in is None and not self.use_esp32_indoor:
                self.init_indoor_dht22()
            temperature, humidity = self._read_dht22(self._dht_in, "indoor")
            if temperature is not None:
                self.indoor_source = "dht22_local"

        if temperature is not None and humidity is not None:
            self.last_reading_at = monotonic() - self._payload_ages.get(self.indoor_sensors.primary_key, 0.0)

        # Median-filter the indoor readings the control decision depends on.
        temperature = self.indoor_temp.push(temperature)
        humidity = self.indoor_humidity.push(humidity)

        if temperature is None or humidity is None:
            return None

        # The raw values go into the row too: a correction can clamp humidity at
        # 0 or 100, and then inverting it no longer recovers what the sensor said.
        raw_temperature, raw_humidity = temperature, humidity
        temperature, humidity = self.indoor_calibration.apply(temperature, humidity)

        data = {
            "temperature": round(temperature, 2),
            "humidity": round(humidity, 2),
            "temperature_raw": round(raw_temperature, 2),
            "humidity_raw": round(raw_humidity, 2),
            "vpd": calculate_vpd(temperature, humidity),
            "indoor_source": self.indoor_source,
        }

        # Read from the cache the background thread fills, never from the
        # sensor: the DHT22 outside retries with sleeps between attempts, and
        # paying ~2 s of that per cycle left the relays unsupervised for a
        # number that feeds a chart and no decision at all.
        outside_temp, outside_humidity = self.outdoor_reading()
        if outside_temp is not None and outside_humidity is not None:
            data["outside_temperature_raw"] = round(outside_temp, 2)
            data["outside_humidity_raw"] = round(outside_humidity, 2)
            outside_temp, outside_humidity = self.outdoor_calibration.apply(
                outside_temp, outside_humidity)
            data["outside_temperature"] = round(outside_temp, 2)
            data["outside_humidity"] = round(outside_humidity, 2)
        else:
            # No outdoor reading. Leave the fields empty rather than copying the
            # indoor values, which used to make the charts show a fake outdoor
            # curve identical to the indoor one.
            data["outside_temperature"] = None
            data["outside_humidity"] = None

        return data

    @property
    def reading_age_seconds(self) -> float:
        """Seconds since the last successful raw indoor read (inf if never)."""
        if self.last_reading_at is None:
            return float("inf")
        return monotonic() - self.last_reading_at

    def publish_status(self, indoor_ok: bool, indoor_error: Optional[str] = None) -> None:
        status = {
            "indoor": {"ok": indoor_ok, "error": indoor_error, "source": self.indoor_source},
            # Judged on whether there is a fresh reading, not on whether the
            # sensor object exists. An initialised DHT22 that answers nothing
            # used to report "ok" while the charts drew an empty outdoor curve.
            "outdoor": self.outdoor_status(),
        }
        if self.drift_report is not None:
            # A drifting sensor still reads "ok": it answers, in range, on time.
            # That is the whole point of the detector, so it gets its own entry
            # rather than flipping a flag nothing else would set.
            report = self.drift_report
            status["drift"] = {
                "ok": report.ok,
                "diverged": report.diverged,
                "suspects": list(report.suspects),
                "messages": report.messages,
                "flags": {name: list(flags) for name, flags in report.flags.items()},
                # Both offsets, so the dashboard can tell "the pair agrees"
                # from "no baseline has been learned yet". Without them a
                # monitor that has never had enough paired samples looks
                # exactly like one that is perfectly happy.
                "baseline": _offset_dict(report.baseline),
                "offset": _offset_dict(report.offset),
            }
        _set_redis("sensor_status", json.dumps(status))

    def close(self) -> None:
        # The reader thread owns `_dht_out`, so it is stopped before the device
        # is released rather than after.
        self.stop_outdoor_reader()
        for sensor in (self._dht_in, self._dht_out):
            try:
                if sensor is not None:
                    sensor.exit()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Setpoints
# ---------------------------------------------------------------------------


#: Floor for the control deadband, so a narrow stage band still rejects sensor noise.
MIN_DEADBAND_PCT = 1.5


@dataclass(frozen=True)
class Setpoint:
    """What the controller should aim for, derived from the stage's own band."""

    target_humidity: float
    deadband_pct: float
    humidity_band: Tuple[float, float]
    leaf_temperature: float
    leaf_vpd: float
    vpd_in_range: bool


def resolve_setpoint(stage: str, air_temperature_c: float, humidity: float) -> Setpoint:
    """
    Translate a stage into a humidity setpoint and deadband.

    Every stage is expressed as a humidity band of the air: VPD stages convert
    their kPa band into the humidity that puts the *leaf* inside it, `dry` has
    an explicit band. The setpoint is the band's midpoint and the deadband is
    its half-width, so "inside the deadband" and "inside the stage's range" are
    the same statement and the controller never works to tighten something that
    is already in spec.

    The leaf sits LEAF_TEMP_OFFSET_C below the air. Leaf VPD is the leaf's
    saturation pressure minus the vapour actually in the air (see
    calculate_leaf_vpd), so both the setpoint and the in-range check use the
    leaf temperature for the VPD and the air temperature for the humidity.
    """
    leaf_temperature = round(air_temperature_c - LEAF_TEMP_OFFSET_C, 1)
    leaf_vpd = calculate_leaf_vpd(air_temperature_c, leaf_temperature, humidity)

    explicit = humidity_range_for_stage(stage)
    if explicit is not None:
        low, high = explicit
        in_range = low <= humidity <= high
    else:
        low, high = sorted(
            humidity_range_bounds_for_stage(stage, air_temperature_c, leaf_temperature)
        )
        in_range = vpd_is_in_range(leaf_vpd, stage)

    return Setpoint(
        target_humidity=round((low + high) / 2, 1),
        deadband_pct=max((high - low) / 2, MIN_DEADBAND_PCT),
        humidity_band=(low, high),
        leaf_temperature=leaf_temperature,
        leaf_vpd=leaf_vpd,
        vpd_in_range=in_range,
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

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

                    if new_stage not in STAGES:
                        if new_stage != unknown_stage_warned:
                            unknown_stage_warned = new_stage
                            print(f"❌ Grow has an unknown stage {new_stage!r}. "
                                  f"Valid stages: {', '.join(STAGES)}. Outputs stay off.")
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

                setpoint = resolve_setpoint(stage, temperature, humidity)

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
