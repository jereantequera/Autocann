"""
When the next watering is due.

A pure function of the last watering date, the interval and today. Nothing is
stored about the next one: it is always counted from the last watering, so a
missed day shows as overdue and the due date moves forward on its own.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, Optional

#: The interval a grow can be given, in days. Shared with the API, which refuses
#: anything outside it.
MIN_INTERVAL_DAYS = 1
MAX_INTERVAL_DAYS = 30


def next_due(last_irrigation_date: Optional[date], interval_days: int, today: date) -> Dict:
    """
    Where the schedule stands today.

    `state` is:
    - "no_history": nothing was ever watered, so it is due today;
    - "today": due today;
    - "upcoming": due in `days_until` days;
    - "overdue": due `overdue_days` days ago, and still not watered.

    `due_date` is an ISO date. With no history it is today, so the card can say
    "toca regar hoy" without a special case.
    """
    if not MIN_INTERVAL_DAYS <= interval_days <= MAX_INTERVAL_DAYS:
        raise ValueError(
            f"interval_days must be {MIN_INTERVAL_DAYS}..{MAX_INTERVAL_DAYS}, got {interval_days!r}"
        )

    if last_irrigation_date is None:
        return {
            "due_date": today.isoformat(),
            "days_until": 0,
            "overdue_days": 0,
            "state": "no_history",
        }

    due = last_irrigation_date + timedelta(days=interval_days)
    days_until = (due - today).days
    if days_until > 0:
        state = "upcoming"
    elif days_until == 0:
        state = "today"
    else:
        state = "overdue"

    return {
        "due_date": due.isoformat(),
        "days_until": days_until,
        "overdue_days": max(0, -days_until),
        "state": state,
    }
