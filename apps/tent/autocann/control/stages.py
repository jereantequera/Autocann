"""
Stage timeline arithmetic, as pure functions.

Counting is done in **calendar days in the local timezone**, not in 24-hour
blocks: a grower says "day 12 of flowering" meaning the twelfth date since the
switch, regardless of what time of day it happened. The date a stage starts is
day 1.

Nothing here touches the database or the clock unless the caller passes one in,
so every rule below is testable on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from autocann.control.vpd_math import STAGE_EXPECTED_DAYS
from autocann.time import ARGENTINA_TZ


def _local_date(timestamp: int, tz=ARGENTINA_TZ):
    return datetime.fromtimestamp(timestamp, tz).date()


def day_number(started_at: int, now: int, tz=ARGENTINA_TZ) -> int:
    """
    Which day of a stage (or grow) `now` falls on, counting the start date as 1.

    Returns 0 when `now` is before the start date, so a future start never reads
    as if it were already running.

    Counting dates rather than elapsed seconds is deliberate: a stage switched at
    23:59 is on day 2 one minute later, which is what a calendar says and what a
    grower expects. Elapsed-time arithmetic would also drift across the DST
    changes that Argentina may reintroduce.
    """
    days = (_local_date(now, tz) - _local_date(started_at, tz)).days
    return days + 1 if days >= 0 else 0


@dataclass(frozen=True)
class StagePeriod:
    """One stage of a grow, with its duration up to `now` or until it ended."""

    stage: str
    started_at: int
    ended_at: Optional[int]
    days: int
    expected_days: Optional[int]
    is_current: bool

    @property
    def progress(self) -> Optional[float]:
        """How far through the expected duration, capped at 1.0. None if unknown."""
        if not self.expected_days:
            return None
        return min(self.days / self.expected_days, 1.0)


def build_timeline(
    events: Sequence[Dict],
    now: int,
    tz=ARGENTINA_TZ,
    expected: Optional[Dict[str, int]] = None,
) -> List[StagePeriod]:
    """
    Turn stage-change events into periods with durations.

    `events` is a sequence of dicts with at least `stage` and `started_at`, in
    any order. Each period ends where the next one starts; the last one is open
    and measured against `now`.
    """
    expected = STAGE_EXPECTED_DAYS if expected is None else expected
    ordered = sorted(events, key=lambda e: e["started_at"])
    if not ordered:
        return []

    periods: List[StagePeriod] = []
    for index, event in enumerate(ordered):
        is_last = index == len(ordered) - 1
        started_at = int(event["started_at"])
        ended_at = None if is_last else int(ordered[index + 1]["started_at"])

        if ended_at is None:
            days = day_number(started_at, now, tz)
        else:
            # A closed period's last day is the day before the next stage began,
            # unless both happened on the same date — then it lasted one day.
            days = max(day_number(started_at, ended_at, tz) - 1, 1)

        stage = event["stage"]
        periods.append(
            StagePeriod(
                stage=stage,
                started_at=started_at,
                ended_at=ended_at,
                days=days,
                expected_days=expected.get(stage),
                is_current=is_last,
            )
        )
    return periods


def current_period(periods: Sequence[StagePeriod]) -> Optional[StagePeriod]:
    for period in periods:
        if period.is_current:
            return period
    return None


def estimated_remaining_days(periods: Sequence[StagePeriod],
                             expected: Optional[Dict[str, int]] = None) -> Optional[int]:
    """
    Days left until the end of `dry`, from the current stage onwards.

    Returns None when the current stage has no expected duration. The estimate
    is indicative: it assumes the remaining stages run their typical length.
    """
    expected = STAGE_EXPECTED_DAYS if expected is None else expected
    current = current_period(periods)
    if current is None or not current.expected_days:
        return None

    order = list(expected.keys())
    try:
        index = order.index(current.stage)
    except ValueError:
        return None

    remaining = max(current.expected_days - current.days, 0)
    remaining += sum(expected.get(stage, 0) for stage in order[index + 1:])
    return remaining


def estimated_end_timestamp(periods: Sequence[StagePeriod], now: int,
                            expected: Optional[Dict[str, int]] = None) -> Optional[int]:
    """Epoch timestamp of the estimated end of the grow, or None."""
    remaining = estimated_remaining_days(periods, expected)
    if remaining is None:
        return None
    return now + remaining * 24 * 3600


def timeline_to_dicts(periods: Sequence[StagePeriod], tz=ARGENTINA_TZ) -> List[Dict]:
    """JSON-friendly view of a timeline, for the API."""
    return [
        {
            "stage": p.stage,
            "started_at": p.started_at,
            "started_date": _local_date(p.started_at, tz).isoformat(),
            "ended_at": p.ended_at,
            "ended_date": _local_date(p.ended_at, tz).isoformat() if p.ended_at else None,
            "days": p.days,
            "expected_days": p.expected_days,
            "progress": p.progress,
            "is_current": p.is_current,
        }
        for p in periods
    ]
