"""
Quiet hours: a window in which an output is not allowed to start.

What the code calls the "dehumidifier" on this installation is an external
extraction fan, and it is loud. Running it at four in the morning is a cost with
no counterpart, so this is the window in which the controller may not choose it.

It constrains the **decision**, not the relay. Forcing a contact open behind the
controller's back would leave its minimum-time bookkeeping describing a device
that is not running: it would believe the fan had been going all night, and the
first command after the window could short-cycle the real one. Here the
controller simply never chooses the action, so every one of its timers stays
true.

Two things are deliberately outside the window's reach:

- **A manual command.** Someone switching a fan on at 4 AM has decided
  something this cannot know about.
- **Stopping.** The window blocks starting, never stopping, and never cuts a
  minimum on-time short. A device already running when the window opens finishes
  the minimum that protects it and then stays off.

The humidity escape exists because silence is not worth a crop. A night at 85 %
is mould, and a window with no way out would be choosing quiet over the plants.
Leave `max_humidity` unset for a hard block.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any, Dict, FrozenSet, Mapping, Optional

from autocann.control.humidity import DEHUMIDIFY, HUMIDIFY

#: Which output each controller action energises. One spelling, shared with the
#: relay layer, so a window configured against an output name can be turned into
#: the action the controller must not choose.
ACTION_OUTPUTS = {HUMIDIFY: "humidity_up", DEHUMIDIFY: "humidity_down"}


def parse_hhmm(value: Any) -> Optional[time]:
    """
    "HH:MM" as a time, or None when it is not one.

    None rather than an exception: this parses stored configuration, and a
    corrupt row must not stop the control loop from running. A window that
    cannot be read is a window that does not apply.
    """
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


@dataclass(frozen=True)
class QuietWindow:
    """One output's quiet window."""

    enabled: bool = False
    start: Optional[time] = None
    end: Optional[time] = None
    #: Humidity at or above which the window is ignored. None is a hard block.
    max_humidity: Optional[float] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "QuietWindow":
        max_humidity = raw.get("max_humidity")
        try:
            max_humidity = None if max_humidity is None else float(max_humidity)
        except (TypeError, ValueError):
            max_humidity = None
        return cls(
            enabled=bool(raw.get("enabled")),
            start=parse_hhmm(raw.get("start")),
            end=parse_hhmm(raw.get("end")),
            max_humidity=max_humidity,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "start": None if self.start is None else self.start.strftime("%H:%M"),
            "end": None if self.end is None else self.end.strftime("%H:%M"),
            "max_humidity": self.max_humidity,
        }

    @property
    def usable(self) -> bool:
        """A window with no bounds is configuration someone started and left."""
        return self.enabled and self.start is not None and self.end is not None

    def contains(self, now: time) -> bool:
        """
        Whether `now` falls inside the window.

        The interesting case is the only one anybody actually wants: a window
        that crosses midnight. 23:00→07:00 is two intervals on a clock face and
        one continuous night, and treating it as `start <= now < end` would make
        it match nothing at all.
        """
        if not self.usable:
            return False
        if self.start == self.end:
            # Zero width. Reading it as "the whole day" would silence an output
            # forever from what looks like an unfinished edit.
            return False
        if self.start < self.end:
            return self.start <= now < self.end
        return now >= self.start or now < self.end

    def blocks(self, now: time, humidity: Optional[float]) -> bool:
        """Whether this window is holding its output off right now."""
        if not self.contains(now):
            return False
        if self.max_humidity is not None and humidity is not None:
            if humidity >= self.max_humidity:
                return False
        return True


def windows_from_dict(raw: Any) -> Dict[str, QuietWindow]:
    """Stored configuration as windows, keyed by output name."""
    if not isinstance(raw, dict):
        return {}
    return {
        name: QuietWindow.from_dict(value)
        for name, value in raw.items()
        if isinstance(value, dict)
    }


def blocked_actions(
    windows: Mapping[str, QuietWindow],
    now: time,
    humidity: Optional[float] = None,
) -> FrozenSet[str]:
    """
    The controller actions the quiet windows forbid right now.

    Expressed as actions rather than outputs because that is what the decision
    is made in. An output nothing drives automatically — ventilation, today —
    simply has no action, so a window on it blocks nothing; that is honest
    rather than silently ignored, and the dashboard says so.
    """
    return frozenset(
        action
        for action, output in ACTION_OUTPUTS.items()
        if output in windows and windows[output].blocks(now, humidity)
    )
