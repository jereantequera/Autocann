"""
Writing a simulated grow's history into a sandbox database, through the store.

The store reads its database path once, at import, from the environment. The
simulator process may have no AUTOCANN_DB set at all, so this module points
`store.connection.DB_PATH` at the sandbox file for the duration of the writes,
and every store function opens its connections through that name.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from autocann.paths import DATA_DIR
from autocann.sim.history import HistoryPlan, default_gaps, plan_history


def check_seed_path(path: Path, sandbox_dir: Optional[Path]) -> Path:
    """
    The database file history may be written to, or ValueError.

    Never inside the real data directory. With `sandbox_dir` (the simulator's
    own temp directory) the file must be inside it. With None the user passed
    `--db` explicitly, so any other path is accepted.
    """
    resolved = path.expanduser().resolve()
    data_dir = DATA_DIR.resolve()
    if resolved == data_dir or data_dir in resolved.parents:
        raise ValueError(f"refusing {resolved}: it is inside the real data directory")
    if sandbox_dir is not None and sandbox_dir.resolve() not in resolved.parents:
        raise ValueError(f"refusing {resolved}: history only goes into the simulator's directory")
    return resolved


@contextmanager
def _sandbox_store(db_path: Path) -> Iterator[None]:
    """Point the store at `db_path` while the block runs, then put it back."""
    from autocann.store import connection, schema

    saved_path, saved_ready = connection.DB_PATH, schema._schema_ready
    connection.DB_PATH = db_path
    schema._schema_ready = False  # the schema is ensured once per process; this is another file
    try:
        yield
    finally:
        connection.DB_PATH = saved_path
        schema._schema_ready = saved_ready


def seed_history(db_path: Path, plan: HistoryPlan, *, grow_id: Optional[int] = None,
                 sandbox_dir: Optional[Path] = None) -> int:
    """
    Write `plan` into the active grow of `db_path` (or `grow_id`). Returns the grow id.

    Order matters: the grow is moved back to the first sample first, so the
    stage events and rows that follow sit inside its life. Raises ValueError
    when the path is refused or a store write fails.
    """
    from autocann.store.active_grow import get_active_grow
    from autocann.store.grow_backdate import backdate_grow
    from autocann.store.grows import record_stage_event
    from autocann.store.irrigations import record_irrigation
    from autocann.store.samples import store_sensor_sample

    path = check_seed_path(Path(db_path), sandbox_dir)
    if not plan.rows:
        raise ValueError("there is no history to seed")
    with _sandbox_store(path):
        if grow_id is None:
            active = get_active_grow()
            if active is None:
                raise ValueError("there is no active grow to seed")
            grow_id = int(active["id"])
        final_stage = plan.stage_changes[-1][1] if plan.stage_changes else plan.start_stage
        if not backdate_grow(grow_id, plan.rows[0]["timestamp"], final_stage):
            raise ValueError("could not move the grow back to the start of the history")
        for at, stage in plan.stage_changes:
            if not record_stage_event(grow_id, stage, started_at=at, notes="Historial simulado"):
                raise ValueError(f"could not record the change to {stage}")
        for row in plan.rows:
            if not store_sensor_sample(row, grow_id=grow_id, at=row["timestamp"]):
                raise ValueError("could not store a sample")
        for w in plan.irrigations:
            if record_irrigation(grow_id, w["litres"], w["items"], ph=w["ph"], ec=w["ec"],
                                 stage=w["stage"], week=w["week"], notes=w["notes"],
                                 timestamp=w["timestamp"]) is None:
                raise ValueError("could not record an irrigation")
    return grow_id


def seed_sandbox_history(db_path: Path, days: int, *, with_gaps: bool, now: int,
                         sandbox_dir: Optional[Path]) -> str:
    """Plan and write `days` of history; returns a one-line summary for the console."""
    gaps = default_gaps(days) if with_gaps else None
    plan = plan_history(days, now, gaps=gaps)
    seed_history(db_path, plan, sandbox_dir=sandbox_dir)
    return (f"History: {days} days, {len(plan.rows)} samples, {len(plan.stage_changes)} stage "
            f"change(s), {len(plan.irrigations)} watering(s)"
            + (", two 2 h gaps" if with_gaps else ""))
