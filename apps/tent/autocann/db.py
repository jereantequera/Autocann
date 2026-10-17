"""
Facade over autocann.store, kept so `from autocann import db` keeps working.

New code should import from autocann.store.<area> directly. Mutable state (DB_PATH,
BUSY_TIMEOUT_SECONDS, the schema flag) is not re-exported: patch it on
autocann.store.connection or autocann.store.schema, where the code reads it.
"""

from __future__ import annotations

from autocann.control.vpd_math import VPD_RANGES
from autocann.store.analytics import (
    detect_anomalies,
    get_weekly_report,
)
from autocann.store.calibration import (
    get_all_calibrations,
    get_calibration,
    set_calibration,
)
from autocann.store.connection import _open
from autocann.store.feed import (
    get_feed_plan,
    replace_feed_plan,
)
from autocann.store.grow_stages import get_grow_stages
from autocann.store.grows import (
    _NO_GROW,
    ALL_GROWS,
    _grow_day_counters,
    _resolve_grow_filter,
    create_grow,
    end_grow,
    get_active_grow,
    get_all_grows,
    get_stage_events,
    get_stage_timeline,
    record_stage_event,
    set_active_grow,
    update_grow_stage,
)
from autocann.store.history import (
    get_aggregated_data,
    get_database_stats,
    get_period_summary,
)
from autocann.store.irrigations import (
    delete_irrigation,
    get_irrigations,
    get_logbook,
    last_irrigation_litres,
    record_irrigation,
)
from autocann.store.migrations import (
    _SAMPLE_DETAIL_COLUMNS,
    _backfill_stage_events,
    _migrate_nullable_outdoor_columns,
    _migrate_sample_detail_columns,
    _parse_local_datetime,
)
from autocann.store.profiles import (
    ProfileError,
    ProfileInUse,
    ProfileNotFound,
    ProfileReadOnly,
    copy_profile,
    delete_profile,
    get_profile,
    get_profiles,
    profile_in_use,
    update_profile,
)
from autocann.store.samples import (
    cleanup_old_data,
    get_latest_sensor_data,
    get_sensor_data_range,
    store_control_event,
    store_sensor_sample,
)
from autocann.store.schema import (
    ensure_schema,
    init_database,
)
from autocann.store.seeds import (
    _FEED_PLAN_SEED,
    BUILTIN_PROFILE_ID,
    BUILTIN_PROFILE_NAME,
    BUILTIN_TENT_ID,
    BUILTIN_TENT_NAME,
    _builtin_profile_stage_rows,
    _seed_builtin_profiles,
    _seed_feed_plan,
    _seed_tents,
)
from autocann.store.settings import (
    get_setting,
    set_setting,
)
from autocann.store.tents import (
    get_tent,
    rename_tent,
)
from autocann.store.vpd_score import (
    _ALL_GROWS_IS_NOT_A_GROW,
    _grow_identity,
    _is_all_grows,
    get_vpd_score,
)

__all__ = [
    "ALL_GROWS",
    "BUILTIN_PROFILE_ID",
    "BUILTIN_PROFILE_NAME",
    "BUILTIN_TENT_ID",
    "BUILTIN_TENT_NAME",
    "VPD_RANGES",
    "_ALL_GROWS_IS_NOT_A_GROW",
    "_FEED_PLAN_SEED",
    "_NO_GROW",
    "_SAMPLE_DETAIL_COLUMNS",
    "_backfill_stage_events",
    "_builtin_profile_stage_rows",
    "_grow_day_counters",
    "_grow_identity",
    "_is_all_grows",
    "_migrate_nullable_outdoor_columns",
    "_migrate_sample_detail_columns",
    "_open",
    "_parse_local_datetime",
    "_resolve_grow_filter",
    "_seed_builtin_profiles",
    "_seed_feed_plan",
    "_seed_tents",
    "ProfileError",
    "ProfileInUse",
    "ProfileNotFound",
    "ProfileReadOnly",
    "cleanup_old_data",
    "copy_profile",
    "create_grow",
    "delete_irrigation",
    "delete_profile",
    "detect_anomalies",
    "end_grow",
    "ensure_schema",
    "get_active_grow",
    "get_aggregated_data",
    "get_all_calibrations",
    "get_all_grows",
    "get_calibration",
    "get_database_stats",
    "get_feed_plan",
    "get_grow_stages",
    "get_irrigations",
    "get_latest_sensor_data",
    "get_logbook",
    "get_period_summary",
    "get_profile",
    "get_profiles",
    "get_sensor_data_range",
    "get_setting",
    "get_stage_events",
    "get_tent",
    "get_stage_timeline",
    "get_vpd_score",
    "get_weekly_report",
    "init_database",
    "last_irrigation_litres",
    "profile_in_use",
    "record_irrigation",
    "record_stage_event",
    "rename_tent",
    "replace_feed_plan",
    "set_active_grow",
    "set_calibration",
    "set_setting",
    "store_control_event",
    "store_sensor_sample",
    "update_grow_stage",
    "update_profile",
]
