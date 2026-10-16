"""
What a tent sensor can physically report, and what it cannot.

A DHT22 that fails a read does not always fail loudly: it can answer -40 °C or
80 °C (the ends of its scale) or a humidity over 100 %. Nobody can act on that
as an alert, so such a reading is simply not a reading: the endpoint refuses
it, the control loop treats it as a failed read (the median filter keeps the
last good value and the stale-data failsafe still trips if they keep coming),
nothing of it is stored, and every statistic skips rows stored before this
existed. The limits live here so those four places cannot disagree.
"""

from __future__ import annotations

from typing import Optional

#: A tent is never below -10 °C or above 60 °C with plants alive in it; past
#: that the DHT22 is answering with a failed read (it rails at -40 / 80).
TEMPERATURE_MIN_C = -10.0
TEMPERATURE_MAX_C = 60.0

#: Relative humidity is a percentage: anything outside 0-100 is a broken read.
HUMIDITY_MIN_PERCENT = 0.0
HUMIDITY_MAX_PERCENT = 100.0


def plausible_temperature(value: Optional[float]) -> bool:
    """A missing value is not an impossible one."""
    return value is None or TEMPERATURE_MIN_C <= value <= TEMPERATURE_MAX_C


def plausible_humidity(value: Optional[float]) -> bool:
    return value is None or HUMIDITY_MIN_PERCENT <= value <= HUMIDITY_MAX_PERCENT


def plausible_reading(temperature: Optional[float], humidity: Optional[float]) -> bool:
    """Both halves of a reading; one impossible half makes the pair unusable."""
    return plausible_temperature(temperature) and plausible_humidity(humidity)


def _between(column: str, low: float, high: float) -> str:
    return f"({column} IS NULL OR {column} BETWEEN {low} AND {high})"


#: The rows every statistic reads: indoor values, and the interval's envelope,
#: inside the limits. Rows stored before the readings were filtered at the
#: source can hold a failed read in either, and one 85 °C row moves a daily
#: average by a degree. The rows stay in the table; they are only not counted.
VALID_ROW_SQL = " AND ".join([
    _between("temperature", TEMPERATURE_MIN_C, TEMPERATURE_MAX_C),
    _between("temperature_min", TEMPERATURE_MIN_C, TEMPERATURE_MAX_C),
    _between("temperature_max", TEMPERATURE_MIN_C, TEMPERATURE_MAX_C),
    _between("humidity", HUMIDITY_MIN_PERCENT, HUMIDITY_MAX_PERCENT),
    _between("humidity_min", HUMIDITY_MIN_PERCENT, HUMIDITY_MAX_PERCENT),
    _between("humidity_max", HUMIDITY_MIN_PERCENT, HUMIDITY_MAX_PERCENT),
])

#: The same, ready to append to a WHERE clause.
AND_VALID_ROW = f" AND {VALID_ROW_SQL}"
