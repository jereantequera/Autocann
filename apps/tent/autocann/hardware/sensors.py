"""
The `Sensors` object the control loop reads: calibrated indoor and outdoor values, and the
status it publishes for the dashboard.
"""

from __future__ import annotations

import json
import threading
from time import monotonic
from typing import Any, Dict, Optional

from autocann.config import IndoorSensors
from autocann.control.drift import DriftMonitor, DriftReport
from autocann.control.humidity import MedianFilter
from autocann.control.sampling import Calibration
from autocann.control.vpd_math import calculate_vpd
from autocann.db import get_calibration
from autocann.hardware.dht import DHT22Reader
from autocann.hardware.indoor import IndoorReader
from autocann.hardware.outdoor import OutdoorReader, OutdoorReading
from autocann.runtime.redis import _set_redis


def _offset_dict(offset) -> Optional[Dict[str, float]]:
    """A drift Offset as plain JSON, or None when there is not one yet."""
    if offset is None:
        return None
    return {"temperature": round(offset.temperature, 2), "humidity": round(offset.humidity, 2)}


class Sensors(DHT22Reader, IndoorReader, OutdoorReader):
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
