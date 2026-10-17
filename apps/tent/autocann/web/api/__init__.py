"""
The JSON API, one Flask blueprint per area.

create_app() registers ALL_BLUEPRINTS. Each module holds its own routes and
nothing else; what they share (the Redis client, helpers) lives in
autocann/web/deps.py and autocann/web/helpers.py.
"""

from __future__ import annotations

from autocann.web.api.analytics import bp as analytics_bp
from autocann.web.api.feeding import bp as feeding_bp
from autocann.web.api.grows import bp as grows_bp
from autocann.web.api.history import bp as history_bp
from autocann.web.api.irrigation_schedule import bp as irrigation_schedule_bp
from autocann.web.api.logbook import bp as logbook_bp
from autocann.web.api.outputs import bp as outputs_bp
from autocann.web.api.profiles import bp as profiles_bp
from autocann.web.api.sensors import bp as sensors_bp
from autocann.web.api.system import bp as system_bp
from autocann.web.api.tents import bp as tents_bp
from autocann.web.api.test_mode import bp as test_mode_bp

ALL_BLUEPRINTS = (
    system_bp,
    grows_bp,
    sensors_bp,
    outputs_bp,
    test_mode_bp,
    history_bp,
    analytics_bp,
    logbook_bp,
    feeding_bp,
    profiles_bp,
    tents_bp,
    irrigation_schedule_bp,
)
