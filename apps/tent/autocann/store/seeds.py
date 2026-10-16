"""
Rows the schema ships with: the default feed plan and the built-in crop profile.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Dict, List, Tuple

from autocann.control.feeding import WATER_ONLY_STAGES
from autocann.control.vpd_math import HUMIDITY_RANGES, STAGE_EXPECTED_DAYS, STAGE_NAMES, VPD_RANGES
from autocann.time import ARGENTINA_TZ

#: The Advanced Nutrients pH Perfect chart, mapped onto this project's stages.
#:
#: The printed chart runs four weeks of "crecimiento" and eight of floración,
#: while a grow here passes through early_veg (3 weeks) and late_veg (4), so the
#: base ramp 1-2-3-4 mL/L lands on the three early weeks and then holds at 4 for
#: the whole of late veg. The last week of flowering is deliberately empty: that
#: is the flush.
#:
#: Only a starting point. It is written once, on an empty database, and is
#: editable from the dashboard afterwards.
_FEED_PLAN_SEED: Tuple[Tuple[str, str, Dict[str, Dict[int, float]]], ...] = (
    ("pH Perfect Grow", "ml/L", {
        "early_veg": {1: 1, 2: 2, 3: 3},
        "late_veg": {1: 4, 2: 4, 3: 4, 4: 4},
        "flowering": {1: 4, 2: 4, 3: 4, 4: 4, 5: 4, 6: 4, 7: 4},
    }),
    ("pH Perfect Micro", "ml/L", {
        "early_veg": {1: 1, 2: 2, 3: 3},
        "late_veg": {1: 4, 2: 4, 3: 4, 4: 4},
        "flowering": {1: 4, 2: 4, 3: 4, 4: 4, 5: 4, 6: 4, 7: 4},
    }),
    ("pH Perfect Bloom", "ml/L", {
        "early_veg": {1: 1, 2: 2, 3: 3},
        "late_veg": {1: 4, 2: 4, 3: 4, 4: 4},
        "flowering": {1: 4, 2: 4, 3: 4, 4: 4, 5: 4, 6: 4, 7: 4},
    }),
    ("B-52", "ml/L", {
        "early_veg": {2: 2, 3: 2},
        "late_veg": {1: 2, 2: 2, 3: 2, 4: 2},
        "flowering": {1: 2, 2: 2, 3: 2, 4: 2},
    }),
    ("Bud Candy", "ml/L", {
        "early_veg": {2: 2, 3: 2},
        "late_veg": {1: 2, 2: 2, 3: 2, 4: 2},
        "flowering": {1: 2, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2, 7: 2},
    }),
    ("Big Bud", "ml/L", {
        "flowering": {2: 2, 3: 2, 4: 2},
    }),
    ("Overdrive", "ml/L", {
        "flowering": {6: 2, 7: 2},
    }),
    ("Rhino Skin", "ml/L", {
        "early_veg": {3: 2},
        "late_veg": {1: 2, 2: 2, 3: 2, 4: 2},
        "flowering": {1: 2, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2},
    }),
    ("Extracto de algas", "ml/L", {
        "flowering": {2: 2, 3: 2, 4: 2, 5: 2, 6: 2},
    }),
    ("Micorrizas", "g/L", {
        "early_veg": {1: 1},
    }),
)


def _seed_feed_plan(conn: sqlite3.Connection) -> None:
    """
    Write the default feed chart, once, into an empty products table.

    Skipped as soon as there is a single product, so a grower who deletes a
    product they do not own never sees it reappear.

    The count and the inserts share one BEGIN IMMEDIATE transaction because the
    web app and the control loop both call ensure_schema() on startup and
    systemd starts them together. Counting outside a write lock lets both read
    an empty table on a fresh install and seed it, leaving the chart with every
    product twice. The loser of the lock waits out busy_timeout, then sees the
    rows and returns.
    """
    conn.commit()  # an earlier migration may still hold an implicit transaction
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("SELECT COUNT(*) FROM feed_products").fetchone()[0]:
            conn.rollback()
            return

        for position, (name, unit, doses) in enumerate(_FEED_PLAN_SEED):
            cursor = conn.execute(
                "INSERT INTO feed_products (name, unit, position) VALUES (?, ?, ?)",
                (name, unit, position),
            )
            product_id = cursor.lastrowid
            conn.executemany(
                "INSERT INTO feed_plan (product_id, stage, week, dose) VALUES (?, ?, ?, ?)",
                [
                    (product_id, stage, week, dose)
                    for stage, weeks in doses.items()
                    for week, dose in weeks.items()
                ],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


#: The built-in profile every install gets. Its stages are built from the
#: constants in `vpd_math`, so the two cannot drift apart.
BUILTIN_PROFILE_ID = "cannabis-photoperiod"


BUILTIN_PROFILE_NAME = "Cannabis fotoperiódico"


#: The tent this rig is, from before tents existed. Its id is the one every
#: pre-tent grow is linked to. Renaming it keeps the id.
BUILTIN_TENT_ID = 1
BUILTIN_TENT_NAME = "Carpa principal"


def _seed_tents(conn: sqlite3.Connection) -> None:
    """
    Write tent 1, once. Never overwrites it.

    INSERT OR IGNORE is the whole check: a tent the person renamed keeps its
    new name, and a second start-up inserts nothing.
    """
    conn.execute(
        "INSERT OR IGNORE INTO tents (id, name, created_at) VALUES (?, ?, ?)",
        (
            BUILTIN_TENT_ID,
            BUILTIN_TENT_NAME,
            datetime.now(ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()


def _builtin_profile_stage_rows() -> List[Tuple]:
    """The built-in profile's stages as `profile_stages` rows, in cycle order."""
    rows = []
    for position, (key, (vpd_min, vpd_max)) in enumerate(VPD_RANGES.items()):
        humidity = HUMIDITY_RANGES.get(key)
        rh_min, rh_max = humidity if humidity else (None, None)
        rows.append((
            key,
            STAGE_NAMES[key],
            position,
            STAGE_EXPECTED_DAYS[key],
            vpd_min,
            vpd_max,
            rh_min,
            rh_max,
            int(key in WATER_ONLY_STAGES),
        ))
    return rows


def _seed_builtin_profiles(conn: sqlite3.Connection) -> None:
    """
    Write the built-in profile, once. Never overwrites it.

    Skipped as soon as a profile with its id exists, so a person who edits its
    stages later keeps their changes. The check and the inserts share one
    BEGIN IMMEDIATE transaction for the same reason `_seed_feed_plan` does: the
    web app and the control loop both start the schema, and the loser of the
    lock must see the winner's rows and stop.
    """
    conn.commit()  # an earlier migration may still hold an implicit transaction
    conn.execute("BEGIN IMMEDIATE")
    try:
        exists = conn.execute(
            "SELECT 1 FROM crop_profiles WHERE id = ?", (BUILTIN_PROFILE_ID,)
        ).fetchone()
        if exists:
            conn.rollback()
            return

        conn.execute(
            "INSERT INTO crop_profiles (id, name, kind, created_at) VALUES (?, ?, 'builtin', ?)",
            (
                BUILTIN_PROFILE_ID,
                BUILTIN_PROFILE_NAME,
                datetime.now(ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.executemany(
            "INSERT INTO profile_stages (profile_id, key, name, position, expected_days, "
            "vpd_min, vpd_max, rh_min, rh_max, water_only) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(BUILTIN_PROFILE_ID, *row) for row in _builtin_profile_stage_rows()],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
