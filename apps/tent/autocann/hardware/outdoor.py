"""
Outdoor reading. Read on its own thread at the cadence a chart needs, never on the control path.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from time import monotonic
from typing import Any, Dict, Optional, Tuple

from autocann.hardware.dht import DHT22_OUTDOOR_PIN

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


class OutdoorReader:
    """The outdoor DHT22, read on its own thread and served from a cache
    that expires. Methods only: the state they use is created by `Sensors.__init__`."""

    def init_outdoor_dht22(self) -> None:
        """
        (Re)initialise the outdoor DHT22.

        Only the reader thread calls this, which is what makes `_dht_out` safe
        to touch without a lock.
        """
        self._init_dht22("_dht_out", DHT22_OUTDOOR_PIN, "outdoor")

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
