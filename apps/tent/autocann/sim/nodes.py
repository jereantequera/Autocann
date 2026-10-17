"""
Status and log of the simulator's fake ESP32 nodes, as the firmware sends them.

The builders mirror firmware/sensor_node/sensor_node.ino: the report envelope
of sendReport(), the SensorStats counters, the nodeLogf() lines (copied as the
firmware prints them) and the 30 s report and 5 min heartbeat intervals. The
relay firmware sends no status and no log, so the fake relay sends none either.

POST /api/node/log carries the status and the lines in one body (see
web/diagnostics.py). Pure apart from the clock and the random source, which
are injected so tests can pin them.
"""

from __future__ import annotations

import random
import time
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

#: sensor_node.ino: FW_VERSION and NODE_NAME, as the backend sees them.
FW_VERSION = "2026.10.12"
SENSOR_NODE_NAME = "sensores"
SENSOR_NODE_IP = "192.168.0.50"

#: sensor_node.ino SENSORS table: sensor id and GPIO pin.
SENSOR_PINS: Tuple[Tuple[str, int], ...] = (("a", 32), ("b", 33))

#: REPORT_INTERVAL_MS and HEARTBEAT_MS in sensor_node.ino.
REPORT_INTERVAL_SECONDS = 30.0
HEARTBEAT_INTERVAL_SECONDS = 300.0

#: LOG_CAPACITY and LOG_TEXT_MAX in sensor_node.ino.
LOG_CAPACITY = 24
LOG_TEXT_MAX = 120

#: resetReasonName() for ESP_RST_POWERON.
BOOT_REASON = "encendido"
#: connectWifi() prints this on the serial console only; the node log has no copy.
WIFI_SSID = "red-simulada"
WIFI_CONNECTING_SERIAL_LINE = f"WiFi: conectando a {WIFI_SSID}"

#: Consecutive failed reads before the firmware logs the error, and the length
#: of a simulated dropout (failed reads in a row) that triggers it.
CONSECUTIVE_FAILED_LOGGED = 3
DROPOUT_READS = 3
#: Simulated dropouts start on this share of reads in the drift and spike scenarios.
DROPOUT_CHANCE = 0.02

RSSI_CENTER_DBM = -60
RSSI_NOISE_DBM = 3
FREE_HEAP_CENTER_BYTES = 180_000
FREE_HEAP_NOISE_BYTES = 8_000


@dataclass(frozen=True)
class Reading:
    """One sensor post: the values a sensor node would send."""

    sensor_id: str
    temperature: float
    humidity: float


def build_sensor_payload(reading: Reading) -> Dict[str, Any]:
    """The JSON body `POST /api/sensor/indoor` expects, as the firmware sends it."""
    return {
        "temperature": round(reading.temperature, 2),
        "humidity": round(reading.humidity, 2),
        "sensor_id": reading.sensor_id,
    }


def attempt(send: Callable[[], Any]) -> int:
    """Runs one POST and returns the status the firmware would see: 200, the HTTP code, or -1 with no answer."""
    try:
        send()
        return 200
    except urllib.error.HTTPError as e:
        return e.code
    except (OSError, ValueError):
        return -1


@dataclass
class _SensorCounters:
    """SensorStats in sensor_node.ino, plus the simulated dropout still in progress."""

    sensor_id: str
    pin: int
    reads_ok: int = 0
    reads_failed: int = 0
    consecutive_failed: int = 0
    posts_ok: int = 0
    posts_failed: int = 0
    last_status: int = 0
    last_temperature: Optional[float] = None
    last_humidity: Optional[float] = None
    dropout_left: int = 0


class NodeStatus:
    """The counters and the pending log of one fake node, updated as the node runs."""

    def __init__(
        self,
        name: str,
        *,
        ip: str,
        sensors: Sequence[Tuple[str, int]] = (),
        firmware: Optional[str] = None,
        rng: random.Random,
        dropout_chance: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.ip = ip
        self.firmware = firmware
        self._rng = rng
        self._dropout_chance = dropout_chance
        self._clock = clock
        self._started = clock()
        self._sensors = {sid: _SensorCounters(sid, pin) for sid, pin in sensors}
        self._lines: List[Dict[str, Any]] = []
        self._sending = 0
        self._last_report: Optional[float] = None
        self._last_heartbeat = self._started
        self._report_failing = False

    # -- log -----------------------------------------------------------------

    def _log(self, level: str, text: str) -> None:
        """LogLine: oldest dropped first when the buffer is full, text cut to LOG_TEXT_MAX."""
        if len(self._lines) == LOG_CAPACITY:
            del self._lines[0]
        self._lines.append(
            {
                "ms": self.uptime_ms(),
                "level": level,
                "text": text[: LOG_TEXT_MAX - 1],
            }
        )

    def boot_log(self) -> None:
        """The first two lines of a sensor node's log: the boot, and the connection with its IP and RSSI."""
        self._log("info", f"arranque {FW_VERSION}, motivo: {BOOT_REASON}")
        self._log("info", f"WiFi conectado, ip {self.ip}, senal {self.rssi()} dBm")

    # -- readings and posts ----------------------------------------------------

    def read(self, sensor_id: str, temperature: float, humidity: float) -> bool:
        """One DHT22 read. Returns False for a failed read, which is not posted."""
        c = self._sensors[sensor_id]
        if c.dropout_left == 0 and self._dropout_chance and self._rng.random() < self._dropout_chance:
            c.dropout_left = DROPOUT_READS
        if c.dropout_left > 0:
            c.dropout_left -= 1
            c.reads_failed += 1
            c.consecutive_failed += 1
            if c.consecutive_failed == CONSECUTIVE_FAILED_LOGGED:
                self._log(
                    "error",
                    f"sensor {sensor_id} (GPIO {c.pin}): {CONSECUTIVE_FAILED_LOGGED} lecturas fallidas seguidas",
                )
            return False
        if c.consecutive_failed >= CONSECUTIVE_FAILED_LOGGED:
            self._log("info", f"sensor {sensor_id}: volvio a leer despues de {c.consecutive_failed} fallas")
        c.reads_ok += 1
        c.consecutive_failed = 0
        c.last_temperature = temperature
        c.last_humidity = humidity
        return True

    def posted(self, sensor_id: str, status: int, detail: str = "") -> None:
        """The outcome of one `POST /api/sensor/indoor`, as the firmware counts and logs it."""
        c = self._sensors[sensor_id]
        was_failing = c.last_status not in (0, 200)
        c.last_status = status
        if status != 200:
            c.posts_failed += 1
            self._log("error", f"sensor {sensor_id}: el POST volvio {status}" + (f" {detail}" if detail else ""))
        else:
            c.posts_ok += 1
            if was_failing:
                self._log("info", f"sensor {sensor_id}: el backend vuelve a aceptar las lecturas")

    # -- status report -----------------------------------------------------------

    def uptime_ms(self) -> int:
        return int((self._clock() - self._started) * 1000)

    def rssi(self) -> int:
        return round(RSSI_CENTER_DBM + self._rng.uniform(-RSSI_NOISE_DBM, RSSI_NOISE_DBM))

    def due_report(self) -> Optional[Dict[str, Any]]:
        """The report to send now, or None if one went out less than 30 s ago. Logs the heartbeat when due."""
        now = self._clock()
        if self._last_report is not None and now - self._last_report < REPORT_INTERVAL_SECONDS:
            return None
        self._last_report = now
        if self._sensors and now - self._last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
            self._last_heartbeat = now
            self._log("info", f"{self._heartbeat_text()} (senal {self.rssi()} dBm)")
        self._sending = len(self._lines)
        return self._payload()

    def report_sent(self, status: int) -> None:
        """Drops the lines the backend took; the rest wait for the next report, as in sendReport()."""
        if status == 200:
            del self._lines[: self._sending]
            if self._report_failing:
                self._report_failing = False
                self._log("info", "el reporte de estado vuelve a llegar")
        elif not self._report_failing:
            self._report_failing = True
            self._log("warn", f"no se pudo mandar el reporte de estado (HTTP {status})")
        self._sending = 0

    def _heartbeat_text(self) -> str:
        parts = []
        for c in self._sensors.values():
            value = (
                f"{c.last_temperature:.1f} C {c.last_humidity:.1f} %"
                if c.last_temperature is not None
                else "sin lectura"
            )
            parts.append(f"{c.sensor_id}: {value}")
        return " · ".join(parts)

    def _payload(self) -> Dict[str, Any]:
        """The body of `POST /api/node/log`, keys in the firmware's order."""
        payload: Dict[str, Any] = {"node": self.name}
        if self.firmware is not None:
            payload["firmware"] = self.firmware
        payload.update(
            {
                "uptime_ms": self.uptime_ms(),
                "ip": self.ip,
                "rssi": self.rssi(),
                "free_heap": round(
                    self._rng.uniform(
                        FREE_HEAP_CENTER_BYTES - FREE_HEAP_NOISE_BYTES, FREE_HEAP_CENTER_BYTES + FREE_HEAP_NOISE_BYTES
                    )
                ),
                "wifi_reconnects": 0,
                "boot_reason": BOOT_REASON,
                "sensors": [
                    {
                        "id": c.sensor_id,
                        "pin": c.pin,
                        "reads_ok": c.reads_ok,
                        "reads_failed": c.reads_failed,
                        "consecutive_failed": c.consecutive_failed,
                        "posts_ok": c.posts_ok,
                        "posts_failed": c.posts_failed,
                        "last_status": c.last_status,
                    }
                    for c in self._sensors.values()
                ],
                "lines": [dict(line) for line in self._lines],
            }
        )
        return payload
