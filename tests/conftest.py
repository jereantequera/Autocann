from __future__ import annotations

import pytest

import autocann.db as db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the database layer at a throwaway file."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "_schema_ready", False)
    db.ensure_schema()
    return db
