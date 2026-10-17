"""
One owner per fact: the anomalies, re-read against what /api/health says.

The anomaly detector reads the database, which is the right place to look for
a hole in the history and the wrong place to look for a dead sensor: the two
are the same row count. Health reads Redis and the loop, and knows which one it
is. Left alone, a primary that stops posting was told five times — the sensor
alert, the Sistema table, the panel line, the sensors badge and a folded
`stale_data`.

So each fact has one place that says it on the dashboard (Sistema shows the
state of everything, and is not counted as an alert):

- **Nothing is being stored right now** (`no_data`, `stale_data`) with the
  loop down or the primary not posting is the red banner's: the anomaly is
  dropped. Paused or without a grow, the pause bar or the no-grow notice says
  it, and it is dropped too.
- **The loop running and not saving** stays in the box, as a warning
  re-worded to blame the recorder rather than the sensors that are
  demonstrably alive, with what to do. It does not raise the red banner:
  nothing is uncontrolled, and Sistema's history check keeps saying it there.
- Everything else is about the quality of stored data and stays in the box.

Pure: the report and the health snapshot come in, a report goes out.
"""

from __future__ import annotations

from typing import Dict

from autocann.config import MODE_MONITOR, PRIMARY
from autocann.control.anomalies import history_message, status_of

#: The anomaly types the detector raises about stored rows existing at all.
HISTORY_ANOMALY_TYPES = ("no_data", "stale_data", "data_gap")

#: Of those, the ones that speak in the present tense. A `data_gap` is a hole
#: *between* two stored rows, already in the past, and stays as it is: answering
#: a question about last night with the state of right now would be wrong.
ONGOING_ANOMALY_TYPES = ("no_data", "stale_data")

BROKEN_SENSOR_STATES = ("missing", "stale")


def banner_owner(health: Dict) -> bool:
    """
    Whether the red banner is already saying "nothing is being stored".

    The same conditions the dashboard's `isBroken` raises the banner for: the
    loop down, or the controlling sensor not posting.
    """
    if (health.get("loop") or {}).get("state") == "down":
        return True
    return primary_broken(health)


def primary_broken(health: Dict) -> bool:
    return any(s.get("role") == PRIMARY and s.get("state") in BROKEN_SENSOR_STATES
               for s in health.get("sensors") or [])


def reframe_history_anomalies(report: Dict, health: Dict) -> Dict:
    """
    The report with every fact that health owns taken out, and the rest sorted.

    A re-worded item is a warning, and it moves to `warnings` so that the key it
    sits under and the severity it carries cannot disagree.
    """
    if not isinstance(report, dict):
        return report

    loop_state = (health.get("loop") or {}).get("state")
    paused = loop_state == "paused"
    no_grow = bool(health.get("no_active_grow") or report.get("no_active_grow"))
    owned = banner_owner(health)
    recorder = "el monitoreo" if loop_state == MODE_MONITOR else "el lazo de control"
    sensors_live = any(s.get("state") in ("posting", "suspect")
                       for s in health.get("sensors") or [])

    def rewrite(issue):
        if not isinstance(issue, dict):
            return issue
        kind = issue.get("type")
        if kind not in HISTORY_ANOMALY_TYPES:
            return issue
        if no_grow or paused:
            return None  # the no-grow notice or the pause bar says this
        if kind not in ONGOING_ANOMALY_TYPES:
            return {**issue, "severity": "warning", "about": "history"}
        if owned:
            return None  # the banner says it, with what to do
        if not sensors_live:
            return issue  # no rows, no readings, and no banner: keep it
        return {**issue, "severity": "warning", "about": "history",
                "message": history_message(issue, recorder)}

    # Sorted by the severity each item carries, falling back to the key it came
    # under, which is what the detector means when it leaves severity out.
    critical, rest = [], []
    for key, default in (("anomalies", "critical"), ("warnings", "warning")):
        for issue in report.get(key) or []:
            issue = rewrite(issue)
            if not issue:
                continue
            severity = issue.get("severity", default) if isinstance(issue, dict) else default
            (critical if severity == "critical" else rest).append(issue)

    out = dict(report)
    out["anomalies"], out["warnings"] = critical, rest
    # `status` drives the colour of the whole strip, so it has to be recomputed
    # from what is left: dropping or downgrading every critical item and leaving
    # "critical" behind would keep the banner red with nothing red in it.
    out["status"] = status_of(critical, rest)
    if no_grow and out["status"] == "ok":
        out["status"] = "no_grow"
    if no_grow:
        out["no_active_grow"] = True
    return out
