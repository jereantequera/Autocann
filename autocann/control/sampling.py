"""
Turning a stream of readings into the rows that get stored, as pure functions.

Three things live here, none of which touch the database, the clock or the
hardware:

- **`SampleAccumulator`** — the loop reads every few seconds but only writes to
  SQLite every few minutes. It used to store whichever instantaneous reading
  happened to coincide with the write, throwing away the other ~99. This
  accumulates the interval instead, so the row carries the average *and* the
  min/max that show how much the tent actually swung.
- **`Calibration`** — a per-sensor offset. Cheap sensors disagree with each
  other by whole degrees, and the correction has to be reversible, so the raw
  reading is kept alongside the corrected one.
- **`insert_gaps`** — a chart draws a straight line across a six-hour outage,
  which reads as "the temperature fell smoothly". Breaking the series with a
  null makes the hole visible as a hole.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

#: Quality of a stored sample, worst-wins when summarising an interval.
QUALITY_OK = "ok"
QUALITY_DEGRADED = "degraded"
QUALITY_FAILSAFE = "failsafe"

_QUALITY_RANK = {QUALITY_OK: 0, QUALITY_DEGRADED: 1, QUALITY_FAILSAFE: 2}

#: Measurements summarised with avg/min/max.
_RANGED_FIELDS = ("temperature", "humidity")

#: Measurements summarised with an average only.
_AVERAGED_FIELDS = (
    "vpd", "leaf_temperature", "leaf_vpd",
    "outside_temperature", "outside_humidity",
    "temperature_raw", "humidity_raw",
    "outside_temperature_raw", "outside_humidity_raw",
)

#: Context carried over from the last reading of the interval.
_LAST_FIELDS = ("stage", "indoor_source", "target_humidity")


@dataclass
class _Series:
    """Running total, minimum and maximum for one measurement."""

    total: float = 0.0
    count: int = 0
    minimum: Optional[float] = None
    maximum: Optional[float] = None

    def push(self, value: float) -> None:
        self.total += value
        self.count += 1
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    @property
    def average(self) -> Optional[float]:
        return None if self.count == 0 else self.total / self.count


@dataclass
class SampleAccumulator:
    """
    Accumulates readings between two database writes.

    A reading missing a field simply does not contribute to it, so an outdoor
    sensor that drops out mid-interval leaves the indoor averages untouched
    rather than poisoning them.
    """

    _series: Dict[str, _Series] = field(default_factory=dict)
    _last: Dict[str, Any] = field(default_factory=dict)
    _actions: Counter = field(default_factory=Counter)
    _quality: str = QUALITY_OK
    _count: int = 0

    def push(self, sample: Optional[Dict], *, quality: str = QUALITY_OK,
             control_action: Optional[str] = None) -> None:
        """Add one reading. A None sample still counts towards the quality."""
        self._quality = _worst_quality(self._quality, quality)
        if control_action:
            self._actions[control_action] += 1
        if sample is None:
            return

        self._count += 1
        for name in _RANGED_FIELDS + _AVERAGED_FIELDS:
            value = sample.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                self._series.setdefault(name, _Series()).push(float(value))

        for name in _LAST_FIELDS:
            if sample.get(name) is not None:
                self._last[name] = sample[name]

    @property
    def count(self) -> int:
        """Readings accumulated since the last reset."""
        return self._count

    def build(self, decimals: int = 2) -> Optional[Dict]:
        """
        The row to store, or None when nothing usable was accumulated.

        Returning None rather than a row of zeros matters: an interval where
        every read failed should leave a gap in the history, which is the truth,
        instead of a fabricated sample.
        """
        if self._count == 0 or "temperature" not in self._series:
            return None

        row: Dict[str, Any] = {"sample_n": self._count, "quality": self._quality}

        for name in _RANGED_FIELDS:
            series = self._series.get(name)
            if series is None:
                continue
            row[name] = round(series.average, decimals)
            row[f"{name}_min"] = round(series.minimum, decimals)
            row[f"{name}_max"] = round(series.maximum, decimals)

        for name in _AVERAGED_FIELDS:
            series = self._series.get(name)
            if series is not None:
                row[name] = round(series.average, decimals)

        row.update(self._last)

        # The dominant action answers "what was the controller mostly doing in
        # this window", which is the useful question for an interval summary.
        if self._actions:
            row["control_action"] = self._actions.most_common(1)[0][0]

        return row

    def reset(self) -> None:
        self._series.clear()
        self._last.clear()
        self._actions.clear()
        self._quality = QUALITY_OK
        self._count = 0


def _worst_quality(a: str, b: str) -> str:
    return a if _QUALITY_RANK.get(a, 0) >= _QUALITY_RANK.get(b, 0) else b


@dataclass(frozen=True)
class Calibration:
    """
    Per-sensor correction.

    Applied as a simple additive offset — enough for the drift cheap sensors
    show, and reversible, which a scale factor would make fiddlier.
    """

    temperature_offset: float = 0.0
    humidity_offset: float = 0.0

    @property
    def is_identity(self) -> bool:
        return self.temperature_offset == 0.0 and self.humidity_offset == 0.0

    def apply(self, temperature: Optional[float],
              humidity: Optional[float]) -> tuple:
        """Corrected (temperature, humidity); None passes through untouched."""
        corrected_t = None if temperature is None else temperature + self.temperature_offset
        corrected_h = None if humidity is None else _clamp_humidity(humidity + self.humidity_offset)
        return corrected_t, corrected_h

    def invert(self, temperature: Optional[float],
               humidity: Optional[float]) -> tuple:
        """
        Back to the raw reading.

        Note the asymmetry: humidity is clamped to 0-100 on the way out, so a
        correction that pushes a reading past either end is not recoverable by
        inversion. That is exactly why the raw values are stored rather than
        recomputed.
        """
        raw_t = None if temperature is None else temperature - self.temperature_offset
        raw_h = None if humidity is None else humidity - self.humidity_offset
        return raw_t, raw_h


def _clamp_humidity(value: float) -> float:
    return min(max(value, 0.0), 100.0)


def insert_gaps(points: Sequence[Dict], expected_interval_seconds: float,
                factor: float = 2.0, timestamp_key: str = "timestamp") -> List[Dict]:
    """
    Break a series wherever samples are further apart than expected.

    Returns a new list with a `{timestamp, gap: True}` marker inserted between
    any two points more than `factor * expected_interval_seconds` apart. A chart
    plotting null for those markers shows the outage instead of drawing a
    straight line across it.

    Points are assumed ordered by timestamp; anything without a usable timestamp
    is passed through untouched.
    """
    if expected_interval_seconds <= 0 or factor <= 0:
        return list(points)

    threshold = expected_interval_seconds * factor
    out: List[Dict] = []
    previous: Optional[Dict] = None

    for point in points:
        current_ts = point.get(timestamp_key)
        previous_ts = previous.get(timestamp_key) if previous else None

        if (
            isinstance(current_ts, (int, float))
            and isinstance(previous_ts, (int, float))
            and current_ts - previous_ts > threshold
        ):
            out.append({
                timestamp_key: int((previous_ts + current_ts) / 2),
                "gap": True,
                "gap_seconds": int(current_ts - previous_ts),
            })

        out.append(point)
        previous = point

    return out
