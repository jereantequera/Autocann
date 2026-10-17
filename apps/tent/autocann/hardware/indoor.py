"""
Indoor reading: the ESP32 over Redis (plus its witness for drift) or a local DHT22.
"""

from __future__ import annotations

import json
from typing import Optional, Tuple

from autocann.config import INDOOR_SENSOR_MAX_AGE_SECONDS
from autocann.control.plausible import plausible_reading
from autocann.control.reading_age import parse_esp32_sample
from autocann.hardware.dht import DHT22_INDOOR_PIN
from autocann.runtime.clock import _now_local
from autocann.runtime.redis import _set_redis, redis_client

#: ESP32 data older than this is ignored. Defined in `autocann.config` because
#: the dashboard marks a reading stale with the same number, and a dashboard
#: calling a reading fresh that the loop is refusing to act on is worse than
#: either answer on its own.
ESP32_MAX_AGE_SECONDS = INDOOR_SENSOR_MAX_AGE_SECONDS


#: Redis key holding the learned drift baseline. Written without a TTL on
#: purpose: a sensor drifts over weeks, so a baseline that expired with the
#: other dashboard keys would be re-learned from the drifted sensor and the
#: detector would never fire.
DRIFT_BASELINE_KEY = "indoor_drift_baseline"


class IndoorReader:
    """The indoor reading: the ESP32 posted to Redis (with its optional
    witness and drift check), or a local DHT22. Methods only: the state they use
    is created by `Sensors.__init__`."""

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
