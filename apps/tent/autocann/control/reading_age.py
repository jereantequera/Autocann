"""
How old an ESP32 reading is.

The age belongs to the data, not to the read. The sensor posts a timestamped
payload to Redis, and the control loop may read that same payload on many
cycles. Aging the reading from each read would make a dead sensor look fresh
for as long as its last value stays acceptable, so the failsafe would fire
late. Here the age is measured from the post.
"""

from __future__ import annotations

import json
from typing import NamedTuple, Optional


class Esp32Sample(NamedTuple):
    #: Whole seconds since the post, the figure the freshness limit is checked on.
    age: int
    temperature: Optional[float]
    humidity: Optional[float]
    #: Seconds since the post, never negative. The reading is aged by this.
    data_age: float


def parse_esp32_sample(raw, now: float) -> Esp32Sample:
    """Parse one Redis payload as seen at epoch time `now`. Raises on bad JSON."""
    payload = json.loads(raw)
    posted = float(payload.get("timestamp", 0))
    return Esp32Sample(
        age=int(now) - int(posted),
        temperature=payload.get("temperature"),
        humidity=payload.get("humidity"),
        data_age=max(0.0, now - posted),
    )
