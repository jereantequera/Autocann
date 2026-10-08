"""
Silent-drift detection for the indoor sensors.

The median filter and the stale-data failsafe together catch a sensor that
spikes and a sensor that stops answering. Neither catches the failure mode that
actually destroyed the previous set of sensors: a humidity element degraded by
UV keeps reporting a plausible, stable, in-range number that is simply wrong,
and the control loop obeys it.

Three detectors live here, none of which need a reference instrument:

- **divergence**: two sensors in the same tent read differently for real
  reasons - position, airflow, a genuine gradient - so an offset between them
  proves nothing. A *change* in that offset does. The monitor learns the
  baseline offset while both sensors look healthy, then alarms when it moves.
- **stuck**: zero variation across a long window. Real air never holds that
  still at the sensor's own resolution.
- **railed**: humidity pinned at the bottom or the top of scale for a long
  window.

Divergence says the pair disagrees; it cannot say which one is lying. When one
of them is also flagged stuck or railed, that is the suspect. When neither is,
the report names both and the operator decides.

These detectors only observe. A drifting sensor must not de-energise the tent -
that is the failsafe's job, and a wrong-but-plausible reading is exactly the
case the failsafe cannot see. This is here to tell the operator which sensor to
distrust before the plants do.

Window sizes are expressed in samples, so they scale with the caller's loop
interval: at the control loop's 3 s period the defaults below are roughly ten
minutes to learn a baseline and twenty minutes of silence to call a sensor
stuck.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Deque, Dict, List, Optional, Tuple

#: A sensor whose value has not moved at all for a full window.
STUCK = "stuck"
#: Humidity pinned against 0 % or 100 % for a full window.
RAILED = "railed"
#: The pair's offset has moved away from the baseline it was given.
DIVERGED = "diverged"

PRIMARY = "primary"
WITNESS = "witness"

Reading = Tuple[float, float]


@dataclass(frozen=True)
class DriftConfig:
    """
    Thresholds, all in samples rather than seconds so they follow the loop rate.
    """

    #: Paired samples needed before a baseline offset is trusted.
    baseline_samples: int = 200
    #: Window over which the current offset is measured.
    offset_samples: int = 200
    #: Window that must show zero variation before a sensor is called stuck.
    stuck_samples: int = 400
    #: How far the offset may move from its baseline before it is divergence.
    max_offset_change_c: float = 1.5
    max_offset_change_pct: float = 5.0
    #: Humidity at or beyond these is "against the rail".
    rail_low_humidity: float = 0.5
    rail_high_humidity: float = 99.5
    #: Variation at or below this counts as none. A DHT22 reports 0.1 steps, so
    #: anything that truly never moves is reporting a frozen register.
    stuck_epsilon: float = 1e-9


@dataclass(frozen=True)
class Offset:
    """Witness minus primary. Positive means the witness reads higher."""

    temperature: float
    humidity: float

    def moved_from(self, baseline: "Offset", config: DriftConfig) -> bool:
        return (
            abs(self.temperature - baseline.temperature) > config.max_offset_change_c
            or abs(self.humidity - baseline.humidity) > config.max_offset_change_pct
        )


@dataclass(frozen=True)
class DriftReport:
    """What the monitor saw this cycle."""

    #: Flags per sensor name, e.g. {"primary": ("stuck",), "witness": ()}.
    flags: Dict[str, Tuple[str, ...]]
    #: Learned offset, once enough healthy paired samples have been seen.
    baseline: Optional[Offset]
    #: Current offset over the recent window, once that window is full.
    offset: Optional[Offset]
    #: True once the current offset has moved away from the baseline.
    diverged: bool

    @property
    def ok(self) -> bool:
        return not self.diverged and not any(self.flags.values())

    @property
    def suspects(self) -> Tuple[str, ...]:
        """
        Which sensors to distrust.

        A sensor carrying its own flag is named on its own. Divergence with no
        per-sensor flag cannot be attributed, so both are named: saying "one of
        these two is wrong" is honest, and picking one would not be.
        """
        flagged = tuple(name for name, flags in sorted(self.flags.items()) if flags)
        if flagged:
            return flagged
        if self.diverged:
            return tuple(sorted(self.flags))
        return ()

    @property
    def messages(self) -> List[str]:
        """Human-readable lines for the log. Empty when everything looks fine."""
        lines: List[str] = []
        labels = {STUCK: "valor congelado", RAILED: "pegado al tope de escala"}
        for name, flags in sorted(self.flags.items()):
            for flag in flags:
                lines.append(f"sensor {name}: {labels.get(flag, flag)}")
        if self.diverged and self.baseline is not None and self.offset is not None:
            lines.append(
                "los dos sensores se separaron: el offset pasó de "
                f"{self.baseline.temperature:+.2f}°C/{self.baseline.humidity:+.2f}% a "
                f"{self.offset.temperature:+.2f}°C/{self.offset.humidity:+.2f}%"
            )
        return lines


@dataclass
class _Track:
    """Per-sensor history feeding the stuck and railed detectors."""

    temperatures: Deque[float] = field(default_factory=deque)
    humidities: Deque[float] = field(default_factory=deque)

    def resize(self, size: int) -> None:
        if self.temperatures.maxlen != size:
            self.temperatures = deque(self.temperatures, maxlen=size)
            self.humidities = deque(self.humidities, maxlen=size)

    def push(self, reading: Optional[Reading]) -> None:
        # A missing sample is not evidence of being stuck, but keeping the
        # window contiguous matters: clearing it on every dropped read would let
        # an intermittent sensor hide forever behind a window that never fills.
        if reading is None:
            return
        temperature, humidity = reading
        self.temperatures.append(float(temperature))
        self.humidities.append(float(humidity))

    def flags(self, config: DriftConfig) -> Tuple[str, ...]:
        found: List[str] = []
        if len(self.temperatures) == config.stuck_samples:
            frozen_t = max(self.temperatures) - min(self.temperatures) <= config.stuck_epsilon
            frozen_h = max(self.humidities) - min(self.humidities) <= config.stuck_epsilon
            # Temperature and humidity freezing together is a dead sensor
            # repeating its last answer. Either one alone is enough to distrust
            # it: a tent does not hold a value to the sensor's resolution for
            # twenty minutes.
            if frozen_t or frozen_h:
                found.append(STUCK)
            if all(h <= config.rail_low_humidity for h in self.humidities) or all(
                h >= config.rail_high_humidity for h in self.humidities
            ):
                found.append(RAILED)
        return tuple(found)


class DriftMonitor:
    """
    Watches a primary sensor against a witness.

    The witness never drives control. It exists so the primary can be caught
    lying, which no amount of filtering on the primary alone can do.

    The baseline should outlive the process: a sensor drifts over weeks, so a
    monitor that re-learns its baseline at every restart would silently adopt
    whatever the drifted sensor currently says. Use `to_dict()` / `load()` to
    persist it; this module itself does no I/O.
    """

    def __init__(self, config: Optional[DriftConfig] = None) -> None:
        self.config = config or DriftConfig()
        self._baseline: Optional[Offset] = None
        self._baseline_samples: Deque[Tuple[float, float]] = deque(
            maxlen=self.config.baseline_samples
        )
        self._offsets: Deque[Tuple[float, float]] = deque(maxlen=self.config.offset_samples)
        self._tracks: Dict[str, _Track] = {PRIMARY: _Track(), WITNESS: _Track()}
        for track in self._tracks.values():
            track.resize(self.config.stuck_samples)

    # -- state that is worth persisting ------------------------------------

    @property
    def baseline(self) -> Optional[Offset]:
        return self._baseline

    def to_dict(self) -> Dict[str, Any]:
        if self._baseline is None:
            return {}
        return {
            "baseline_temperature": self._baseline.temperature,
            "baseline_humidity": self._baseline.humidity,
        }

    def load(self, stored: Optional[Dict[str, Any]]) -> bool:
        """
        Adopt a previously persisted baseline. Returns True when one was taken.

        Anything malformed is ignored rather than raised: a corrupt Redis value
        must not stop the control loop from starting.
        """
        if not stored:
            return False
        try:
            self._baseline = Offset(
                temperature=float(stored["baseline_temperature"]),
                humidity=float(stored["baseline_humidity"]),
            )
        except (KeyError, TypeError, ValueError):
            return False
        self._baseline_samples.clear()
        return True

    def reset_baseline(self) -> None:
        """
        Forget the baseline and learn a new one, after swapping a sensor.

        The per-sensor history goes too. It describes the hardware that was
        just removed, and a STUCK flag left over from the dead sensor would
        block the new baseline from ever being learned - the monitor refuses to
        learn one while either sensor looks faulty.
        """
        self._baseline = None
        self._baseline_samples.clear()
        self._offsets.clear()
        for track in self._tracks.values():
            track.temperatures.clear()
            track.humidities.clear()

    # -- the cycle ---------------------------------------------------------

    def update(
        self,
        primary: Optional[Reading] = None,
        witness: Optional[Reading] = None,
    ) -> DriftReport:
        """Feed one cycle's pair of readings and get the current verdict."""
        self._tracks[PRIMARY].push(primary)
        self._tracks[WITNESS].push(witness)
        flags = {name: track.flags(self.config) for name, track in self._tracks.items()}

        if primary is not None and witness is not None:
            pair = (witness[0] - primary[0], witness[1] - primary[1])
            self._offsets.append(pair)
            # A baseline is only meaningful while both sensors look healthy.
            # Learning one from a sensor that is already stuck would enshrine
            # the fault as normal and guarantee the detector never fires.
            if self._baseline is None and not any(flags.values()):
                self._baseline_samples.append(pair)
                if len(self._baseline_samples) == self.config.baseline_samples:
                    self._baseline = self._median_offset(self._baseline_samples)

        offset = (
            self._median_offset(self._offsets)
            if len(self._offsets) == self.config.offset_samples
            else None
        )
        diverged = (
            self._baseline is not None
            and offset is not None
            and offset.moved_from(self._baseline, self.config)
        )

        return DriftReport(
            flags=flags, baseline=self._baseline, offset=offset, diverged=diverged
        )

    @staticmethod
    def _median_offset(samples: Deque[Tuple[float, float]]) -> Offset:
        """
        Median rather than mean, for the same reason the sensor path uses one:
        a handful of bad reads must not move the reference everything else is
        judged against.
        """
        return Offset(
            temperature=median(t for t, _ in samples),
            humidity=median(h for _, h in samples),
        )
