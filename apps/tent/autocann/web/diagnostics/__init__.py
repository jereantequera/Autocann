"""
Is the installation actually working, in one answer — and the nodes' own log.

Two problems, one module, because they are the same problem seen twice.

The dashboard used to answer "¿anda el sensor?" from three sources that could
not agree: the hero badge from the control loop's `sensor_status`, each sensor
card from the age of its own reading in Redis, and the alert strip from the age
of the last row in the database. A paused loop made all three say something
different at the same time — a red "sensor posiblemente desconectado" above two
green "OK" cards, with the sensor posting perfectly well throughout. So the
verdict is computed in one place, from every source at once, and served whole.

The second half is the node's log. The ESP32 prints exactly what is wrong on
its serial port — a failed DHT read, a POST that came back 400, a WiFi that
dropped — and nobody can see it, because reading that port means carrying a
laptop into the tent and unplugging the node from its power supply to plug it
into USB. So the node posts its own log here and the browser shows it.

Nothing in here decides anything about the tent. It only reports.
"""

from __future__ import annotations

from .health import (
    HISTORY_MAX_AGE_SECONDS,
    LEVELS,
    LOOP_MAX_AGE_SECONDS,
    ROLE_LABELS,
    ROLE_SEVERITY,
    describe_age,
    history_verdict,
    loop_verdict,
    overall_verdict,
    sensor_label,
    sensor_verdict,
    worst,
)
from .nodes import (
    LOG_LEVELS,
    LOG_MAX_LINE_LENGTH,
    LOG_MAX_LINES,
    LOG_MAX_LINES_PER_REPORT,
    NODE_INDEX_KEY,
    NODE_LOG_PREFIX,
    NODE_MAX_AGE_SECONDS,
    NODE_STATUS_PREFIX,
    append_log_lines,
    clean_log_lines,
    node_report,
    node_verdict,
)
from .routes import (
    register_diagnostics,
)
from .snapshot import (
    control_mode,
    health_snapshot,
    indoor_delta,
    indoor_entries,
    node_names,
    paused_since,
)

__all__ = [
    "LOOP_MAX_AGE_SECONDS",
    "NODE_MAX_AGE_SECONDS",
    "HISTORY_MAX_AGE_SECONDS",
    "LOG_MAX_LINES",
    "LOG_MAX_LINES_PER_REPORT",
    "LOG_MAX_LINE_LENGTH",
    "LOG_LEVELS",
    "NODE_STATUS_PREFIX",
    "NODE_LOG_PREFIX",
    "NODE_INDEX_KEY",
    "LEVELS",
    "worst",
    "ROLE_LABELS",
    "ROLE_SEVERITY",
    "sensor_label",
    "describe_age",
    "sensor_verdict",
    "loop_verdict",
    "history_verdict",
    "overall_verdict",
    "clean_log_lines",
    "append_log_lines",
    "node_verdict",
    "node_report",
    "control_mode",
    "paused_since",
    "indoor_entries",
    "indoor_delta",
    "node_names",
    "health_snapshot",
    "register_diagnostics",
]
