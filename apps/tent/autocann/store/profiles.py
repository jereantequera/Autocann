"""
Crop profiles: the stage templates a grow can follow.

Reads return what is stored. Writes raise ProfileError subclasses for the
cases the web layer turns into HTTP answers (missing, built-in, in use), and
let database errors propagate.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, Iterator, List, Optional

from autocann.store.connection import _open
from autocann.store.schema import ensure_schema
from autocann.time import ARGENTINA_TZ


class ProfileError(Exception):
    """Base for the profile write refusals."""


class ProfileNotFound(ProfileError):
    """The profile (or the source of a copy) does not exist."""


class ProfileReadOnly(ProfileError):
    """A built-in profile: it can be copied, never edited or deleted."""


class ProfileInUse(ProfileError):
    """An active grow runs with this profile, so it cannot be deleted."""


def get_profiles() -> List[Dict]:
    """Every crop profile, built-in first then by name. Each is `id`, `name`, `kind`."""
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        try:
            rows = conn.execute(
                "SELECT id, name, kind FROM crop_profiles ORDER BY kind = 'builtin' DESC, name ASC"
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]
    except sqlite3.Error as e:
        print(f"Error getting crop profiles: {e}")
        return []


def get_profile(profile_id: str) -> Optional[Dict]:
    """
    One profile with its `stages` in position order, or None when it does not exist.

    Each stage is a dict with every `profile_stages` column; `water_only` is a bool.
    """
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        try:
            profile = conn.execute(
                "SELECT id, name, kind FROM crop_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            if profile is None:
                return None
            stages = conn.execute(
                "SELECT profile_id, key, name, position, expected_days, vpd_min, vpd_max, "
                "rh_min, rh_max, water_only FROM profile_stages "
                "WHERE profile_id = ? ORDER BY position ASC",
                (profile_id,),
            ).fetchall()
        finally:
            conn.close()
        return {
            **dict(profile),
            "stages": [{**dict(row), "water_only": bool(row["water_only"])} for row in stages],
        }
    except sqlite3.Error as e:
        print(f"Error getting crop profile '{profile_id}': {e}")
        return None


def profile_in_use(profile_id: str) -> bool:
    """True when an active grow runs with this profile. Ended grows do not count."""
    ensure_schema()
    conn = _open()
    try:
        return _in_use(conn, profile_id)
    finally:
        conn.close()


def copy_profile(source_id: str, name: str) -> str:
    """
    Copy any profile's stages into a new custom profile named `name`. Returns its id.

    The copy is by value: editing either profile afterwards never changes the other.
    """
    new_id = _new_profile_id(name)
    with _write() as conn:
        if _kind(conn, source_id) is None:
            raise ProfileNotFound(f"profile '{source_id}' does not exist")
        conn.execute(
            "INSERT INTO crop_profiles (id, name, kind, created_at) VALUES (?, ?, 'custom', ?)",
            (new_id, name, _stamp()),
        )
        conn.execute(
            "INSERT INTO profile_stages (profile_id, key, name, position, expected_days, "
            "vpd_min, vpd_max, rh_min, rh_max, water_only) "
            "SELECT ?, key, name, position, expected_days, vpd_min, vpd_max, rh_min, rh_max, "
            "water_only FROM profile_stages WHERE profile_id = ?",
            (new_id, source_id),
        )
    return new_id


def update_profile(profile_id: str, name: str, stages: List[Dict]) -> None:
    """
    Replace a custom profile's name and all its stages, in one transaction.

    `stages` are dicts with `key`, `name`, `expected_days`, the four band
    columns and `water_only`, in the order they should run. The caller validates
    them (autocann.control.profiles). Grows keep their own snapshot, so they are
    not touched.
    """
    with _write() as conn:
        _check_custom(conn, profile_id)
        conn.execute("UPDATE crop_profiles SET name = ? WHERE id = ?", (name, profile_id))
        conn.execute("DELETE FROM profile_stages WHERE profile_id = ?", (profile_id,))
        _write_stages(conn, profile_id, stages)


def delete_profile(profile_id: str) -> None:
    """
    Delete a custom profile and its stages.

    Refused while an active grow uses it. Foreign keys are off in this database,
    so the stages are deleted explicitly rather than by ON DELETE CASCADE.
    """
    with _write() as conn:
        _check_custom(conn, profile_id)
        if _in_use(conn, profile_id):
            raise ProfileInUse(f"profile '{profile_id}' is used by an active grow")
        conn.execute("DELETE FROM profile_stages WHERE profile_id = ?", (profile_id,))
        conn.execute("DELETE FROM crop_profiles WHERE id = ?", (profile_id,))


@contextmanager
def _write() -> Iterator[sqlite3.Connection]:
    """One write transaction: BEGIN IMMEDIATE, commit on success, rollback on any error."""
    ensure_schema()
    conn = _open()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _kind(conn: sqlite3.Connection, profile_id: str) -> Optional[str]:
    row = conn.execute("SELECT kind FROM crop_profiles WHERE id = ?", (profile_id,)).fetchone()
    return None if row is None else row[0]


def _check_custom(conn: sqlite3.Connection, profile_id: str) -> None:
    kind = _kind(conn, profile_id)
    if kind is None:
        raise ProfileNotFound(f"profile '{profile_id}' does not exist")
    if kind != "custom":
        raise ProfileReadOnly(f"profile '{profile_id}' is built-in and cannot be changed")


def _in_use(conn: sqlite3.Connection, profile_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM grows WHERE profile_id = ? AND is_active = 1 LIMIT 1", (profile_id,)
    ).fetchone() is not None


def _write_stages(conn: sqlite3.Connection, profile_id: str, stages: List[Dict]) -> None:
    conn.executemany(
        "INSERT INTO profile_stages (profile_id, key, name, position, expected_days, "
        "vpd_min, vpd_max, rh_min, rh_max, water_only) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                profile_id, stage["key"], stage["name"], position, stage.get("expected_days"),
                stage.get("vpd_min"), stage.get("vpd_max"), stage.get("rh_min"),
                stage.get("rh_max"), 1 if stage.get("water_only") else 0,
            )
            for position, stage in enumerate(stages)
        ],
    )


def _new_profile_id(name: str) -> str:
    """A readable id from the name, plus random hex so two copies of one name differ."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:40].strip("-")
    return f"{slug or 'perfil'}-{secrets.token_hex(4)}"


def _stamp() -> str:
    return datetime.now(ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
