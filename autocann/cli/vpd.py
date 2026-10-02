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
from dataclasses import dataclass, replace
from datetime import datetime
from time import monotonic, sleep
from typing import Any, Dict, Optional, Tuple

import redis

from autocann.config import control_tuning_from_env, redis_config_from_env
from autocann.control.humidity import (
    DEHUMIDIFY,
    HUMIDIFY,
    IDLE,
    ControllerConfig,
    ControllerState,
    MedianFilter,
    decide,
)
from autocann.control.sampling import QUALITY_DEGRADED, QUALITY_FAILSAFE, QUALITY_OK, Calibration, SampleAccumulator
from autocann.control.vpd_math import (
    STAGES,
    calculate_vpd,
    humidity_range_bounds_for_stage,
    humidity_range_for_stage,
    vpd_is_in_range,
)
from autocann.db import ensure_schema, get_active_grow, get_calibration, store_control_event, store_sensor_sample
from autocann.hardware.outputs import get_outputs, manual_override_key, parse_bool_flag
from autocann.time import ARGENTINA_TZ

LOOP_INTERVAL_SECONDS = 3.0
#: How often a sample is persisted to SQLite.
DB_SAVE_INTERVAL_SECONDS = 300.0
#: How often the active grow / stage is re-read from the database.
STAGE_CHECK_INTERVAL_SECONDS = 60.0
#: ESP32 data older than this is ignored.
ESP32_MAX_AGE_SECONDS = 60
#: Leaves run cooler than the air; VPD is judged at leaf temperature.
LEAF_TEMP_OFFSET_C = 1.5
#: Expiry on the Redis keys the dashboard reads, so a dead loop stops looking alive.
REDIS_KEY_TTL_SECONDS = 600
#: Minimum gap between attempts to re-create failed sensor objects.
SENSOR_REINIT_INTERVAL_SECONDS = 30.0
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

_redis_cfg = redis_config_from_env()
redis_client = redis.Redis(host=_redis_cfg.host, port=_redis_cfg.port, db=_redis_cfg.db)


def _now_local() -> datetime:
    return datetime.now(ARGENTINA_TZ)


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

    def __init__(self) -> None:
        self._specs = {o["name"]: o for o in get_outputs()}
        self._devices: Dict[str, Any] = {name: None for name in self._specs}
        self._state: Dict[str, bool] = {name: False for name in self._specs}

    def setup(self) -> None:
        import gpiozero  # imported lazily so this module stays importable off-Pi

        for name, spec in self._specs.items():
            self._devices[name] = gpiozero.OutputDevice(
                int(spec["pin_bcm"]),
                active_high=bool(spec.get("active_high", True)),
                initial_value=False,
            )
            print(f"🔌 {spec['label']} → BCM {spec['pin_bcm']}")
        self.all_off(log_event=False)

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

    def all_off(self, *, log_event: bool = True) -> None:
        """Unconditionally de-energise everything. Overrides do not apply here."""
        for name in self._specs:
            self.set_output(name, False, log_event=log_event)

    def set_output(self, name: str, on: bool, *, log_event: bool = True) -> None:
        spec = self._specs.get(name)
        if spec is None or self._state.get(name) == on:
            return

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
        redis_key = spec.get("redis_key")
        if redis_key:
            _set_redis(redis_key, "true" if on else "false")
        if log_event:
            # Only transitions are recorded. The old loop logged an event on
            # every iteration, which added ~29k rows per day.
            store_control_event(name, "on" if on else "off")

    def close(self) -> None:
        for device in self._devices.values():
            try:
                if device is not None:
                    device.off()
                    device.close()
            except Exception:
                pass


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


class Sensors:
    """Indoor (ESP32 via Redis, or local DHT22) and outdoor (local DHT22) readings."""

    def __init__(self, use_esp32_indoor: bool = True) -> None:
        self.use_esp32_indoor = use_esp32_indoor
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
        self._last_warned: Dict[str, float] = {}

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

    def init_dht22(self) -> None:
        """(Re)initialise the local DHT22 sensors that are actually needed."""
        try:
            import adafruit_dht
        except Exception as e:
            print(f"⚠️ adafruit_dht unavailable, skipping local DHT22 sensors: {e}")
            return

        wanted = [("_dht_out", DHT22_OUTDOOR_PIN, "outdoor")]
        if not self.use_esp32_indoor:
            wanted.append(("_dht_in", DHT22_INDOOR_PIN, "indoor"))

        for attr, gpio_num, label in wanted:
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

    def _read_dht22(self, sensor, name: str) -> Tuple[Optional[float], Optional[float]]:
        if sensor is None:
            return None, None

        for attempt in range(DHT_READ_ATTEMPTS):
            try:
                temperature = sensor.temperature
                humidity = sensor.humidity
                if temperature is not None and humidity is not None:
                    return float(temperature), float(humidity)
            except RuntimeError:
                pass  # a failed checksum is normal for a DHT22
            except Exception as e:
                self._warn_throttled(f"dht_{name}", f"⚠️ DHT22 {name} error: {e}")
            if attempt < DHT_READ_ATTEMPTS - 1:
                sleep(DHT_RETRY_DELAY_SECONDS)

        return None, None

    # -- ESP32 over Redis --------------------------------------------------

    def _read_esp32_indoor(self) -> Tuple[Optional[float], Optional[float]]:
        try:
            raw = redis_client.get("esp32_indoor")
            if raw is None:
                return None, None

            payload = json.loads(raw)
            age = int(_now_local().timestamp()) - int(payload.get("timestamp", 0))
            if age > ESP32_MAX_AGE_SECONDS:
                self._warn_throttled(
                    "esp32_stale",
                    f"⚠️ ESP32 indoor data is stale ({age}s old, max {ESP32_MAX_AGE_SECONDS}s)",
                )
                return None, None

            temperature = payload.get("temperature")
            humidity = payload.get("humidity")
            if temperature is None or humidity is None:
                return None, None
            return float(temperature), float(humidity)
        except Exception as e:
            print(f"⚠️ Error reading ESP32 indoor data: {e}")
            return None, None

    # -- public ------------------------------------------------------------

    def read(self) -> Optional[dict]:
        """
        One reading cycle. Returns None when there is no usable indoor reading,
        which the caller must treat as a failsafe condition.
        """
        temperature = humidity = None

        if self.use_esp32_indoor:
            temperature, humidity = self._read_esp32_indoor()
            if temperature is not None:
                self.indoor_source = "esp32"

        if temperature is None or humidity is None:
            if self._dht_in is None and not self.use_esp32_indoor:
                self.init_dht22()
            temperature, humidity = self._read_dht22(self._dht_in, "indoor")
            if temperature is not None:
                self.indoor_source = "dht22_local"

        if temperature is not None and humidity is not None:
            self.last_reading_at = monotonic()

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

        outside_temp, outside_humidity = self._read_dht22(self._dht_out, "outdoor")
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
            "outdoor": {
                "ok": self._dht_out is not None,
                "error": None if self._dht_out is not None else "DHT22 outdoor not initialised",
            },
        }
        _set_redis("sensor_status", json.dumps(status))

    def close(self) -> None:
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

    Every stage is expressed as a humidity band: VPD stages convert their kPa
    band at leaf temperature, `dry` has an explicit one. The setpoint is the
    band's midpoint and the deadband is its half-width, so "inside the deadband"
    and "inside the stage's range" are the same statement and the controller
    never works to tighten something that is already in spec.

    Both the setpoint and the in-range check are evaluated at leaf temperature.
    They used to disagree — the setpoint came from air temperature while the
    check used leaf temperature — which parked the tent at the wet edge of the
    band instead of its centre.
    """
    leaf_temperature = round(air_temperature_c - LEAF_TEMP_OFFSET_C, 1)
    leaf_vpd = calculate_vpd(leaf_temperature, humidity)

    explicit = humidity_range_for_stage(stage)
    if explicit is not None:
        low, high = explicit
        in_range = low <= humidity <= high
    else:
        low, high = sorted(humidity_range_bounds_for_stage(stage, leaf_temperature))
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

    relays = Relays()
    sensors = Sensors(use_esp32_indoor=use_esp32_indoor)
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
    sensors.init_dht22()

    print("📡 Indoor sensor: ESP32 via the web API" if use_esp32_indoor
          else "🔌 Indoor sensor: local DHT22")
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
    # Every reading feeds this; one row per DB_SAVE_INTERVAL_SECONDS summarises
    # the whole interval instead of whichever reading happened to land on it.
    accumulator = SampleAccumulator()

    try:
        while not _shutdown:
            loop_started = monotonic()

            try:
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
                    # floods the log, so back off between attempts.
                    if loop_started - last_sensor_reinit >= SENSOR_REINIT_INTERVAL_SECONDS:
                        last_sensor_reinit = loop_started
                        print("🔄 Reinitialising sensors...")
                        sensors.init_dht22()
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

                decision = decide(
                    humidity=humidity,
                    target_humidity=setpoint.target_humidity,
                    now=loop_started,
                    state=state,
                    config=replace(config, deadband_pct=setpoint.deadband_pct),
                    reading_age_seconds=sensors.reading_age_seconds,
                )
                sensors_data["control_action"] = decision.action
                sensors_data["control_reason"] = decision.reason
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

                if loop_started - last_db_save >= DB_SAVE_INTERVAL_SECONDS:
                    summary = accumulator.build()
                    if summary is None:
                        # Nothing usable in the whole interval. Leaving a hole is
                        # the honest record; the charts draw it as a break.
                        last_db_save = loop_started
                        accumulator.reset()
                    elif store_sensor_sample(summary):
                        print(f"💾 Muestra guardada: {summary['sample_n']} lecturas, "
                              f"T {summary.get('temperature_min')}–{summary.get('temperature_max')}°C, "
                              f"HR {summary.get('humidity_min')}–{summary.get('humidity_max')}%")
                        last_db_save = loop_started
                        accumulator.reset()

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
        choices=["early_veg", "late_veg", "flowering", "dry"],
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
