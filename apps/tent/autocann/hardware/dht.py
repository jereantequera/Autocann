"""
Local DHT22 access: initialisation, bounded retries and plausibility checks.
"""

from __future__ import annotations

from time import monotonic, sleep
from typing import Optional, Tuple

from autocann.control.plausible import plausible_reading

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


class DHT22Reader:
    """Local DHT22 access shared by the indoor and outdoor readers.

    Methods only: the state they use is created by `Sensors.__init__`."""

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
