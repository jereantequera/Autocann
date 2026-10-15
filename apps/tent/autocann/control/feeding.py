"""
Feeding schedule arithmetic, as pure functions.

A feed chart is a grid: for each product, how much goes in on each week of each
stage. The grower reads it as "floración semana 3, 4 ml/L de cada base". This
module turns a stage plus a day number into that row of the chart, and scales
the doses to the litres actually being mixed.

Weeks are counted inside the **stage**, not the grow: day 1 to 7 of a stage is
week 1. That matches both how feed charts are printed and how
`autocann.control.stages` already counts days.

Nothing here touches the database or the clock, so every rule is testable on
its own.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence

from autocann.control.vpd_math import STAGE_EXPECTED_DAYS


def weeks_in_stage(stage: str, expected: Optional[Dict[str, int]] = None) -> int:
    """
    How many week-columns a stage's chart has.

    Derived from the stage's typical length rather than stored separately, so
    the chart cannot drift from `STAGE_EXPECTED_DAYS`. A stage with no known
    length gets a single column.
    """
    expected = STAGE_EXPECTED_DAYS if expected is None else expected
    days = expected.get(stage)
    if not days or days <= 0:
        return 1
    return max(1, math.ceil(days / 7))


def week_of_stage(day_in_stage: int) -> int:
    """
    Which week of the stage a day falls in; day 1 is week 1.

    Returns 0 for a day that has not arrived yet (`stages.day_number` returns 0
    for a stage that starts in the future), so a future stage never reads as if
    it were already being fed.
    """
    if day_in_stage is None or day_in_stage <= 0:
        return 0
    return math.ceil(day_in_stage / 7)


def effective_week(stage: str, day_in_stage: int,
                   expected: Optional[Dict[str, int]] = None) -> int:
    """
    The chart column to actually use, clamped to the stage's last week.

    A stage that runs longer than its typical length keeps getting its last
    week's doses — which is what the printed charts tell you to do ("repetir la
    última semana"). Clamping against the stage length rather than against the
    weeks that happen to have doses matters for a final flush week: it has no
    doses at all, and without the clamp a grow running past it would fall back
    to the last week that *did* have some and start feeding again.
    """
    week = week_of_stage(day_in_stage)
    if week == 0:
        return 0
    return min(week, weeks_in_stage(stage, expected))


def plan_for_week(plan: Sequence[Dict], stage: str, week: int) -> List[Dict]:
    """
    The products due on one week of one stage, in the plan's own order.

    `plan` is a sequence of product dicts, each with `doses` mapping
    `stage -> {week: dose}`. Products with no dose that week are left out, and a
    week where nothing is due comes back empty — which is an instruction, not a
    hole: see `water_only_reason` for which instruction.
    """
    if week <= 0:
        return []

    due: List[Dict] = []
    for product in plan:
        doses = (product.get("doses") or {}).get(stage) or {}
        dose = doses.get(week, doses.get(str(week)))
        try:
            dose = float(dose)
        except (TypeError, ValueError):
            continue
        if dose <= 0:
            continue
        due.append({
            "product_id": product.get("id"),
            "name": product.get("name"),
            "unit": product.get("unit", "ml/L"),
            "dose": dose,
        })
    return due


#: Stages the chart is meant to leave empty, because they are watered with
#: plain water. A seedling lives off what the substrate already holds: it has
#: no root mass to take up a feed, and a salt it cannot use stays in the medium
#: and burns the first roots that reach it.
WATER_ONLY_STAGES = frozenset({"seedling"})


def water_only_reason(stage: str, week: int, due: Sequence[Dict]) -> Optional[str]:
    """
    Why this week carries no nutrients: `"seedling"`, `"flush"`, or None.

    An empty week reads as two different instructions depending on where it
    falls, and the dashboard has to say which one: plain water because the plant
    is too young to be fed, or plain water to wash the medium out before
    harvest. None means something *is* due.
    """
    if week <= 0 or due:
        return None
    return "seedling" if stage in WATER_ONLY_STAGES else "flush"


def scale_to_volume(items: Iterable[Dict], litres: float) -> List[Dict]:
    """
    Add the amount to actually pour for `litres` of solution.

    The dose is per litre; `total` is what goes in the watering can, rounded to
    a tenth because nobody measures a fertiliser to more than that.
    """
    volume = max(float(litres or 0), 0.0)
    scaled = []
    for item in items:
        dose = float(item.get("dose") or 0)
        scaled.append({**item, "total": round(dose * volume, 1)})
    return scaled


def chart_columns(expected: Optional[Dict[str, int]] = None) -> List[Dict]:
    """
    The stage/week columns of the whole chart, for the editor grid.

    One entry per column, in stage order, so the frontend does not have to
    know how long a stage is.
    """
    expected = STAGE_EXPECTED_DAYS if expected is None else expected
    columns = []
    for stage in expected:
        for week in range(1, weeks_in_stage(stage, expected) + 1):
            columns.append({"stage": stage, "week": week})
    return columns
