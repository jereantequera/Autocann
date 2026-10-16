from __future__ import annotations

import pytest

import autocann.db as db
from autocann.store import connection as store_conn
from autocann.store import schema as store_schema


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the database layer at a throwaway file."""
    monkeypatch.setattr(store_conn, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(store_schema, "_schema_ready", False)
    db.ensure_schema()
    return db


class FakeRedis:
    """Enough of redis-py for the endpoints, and it records TTLs."""

    def __init__(self):
        self.store = {}
        self.ttls = {}

    def get(self, key):
        value = self.store.get(key)
        return value.encode() if isinstance(value, str) else value

    def set(self, key, value, ex=None):
        self.store[key] = value
        self.ttls[key] = ex

    def delete(self, key):
        self.store.pop(key, None)
        self.ttls.pop(key, None)


@pytest.fixture
def client(monkeypatch, temp_db):
    import autocann.web.app as web

    fake = FakeRedis()
    monkeypatch.setattr(web, "pooled_client", lambda *args, **kwargs: fake)
    app = web.create_app()
    app.config.update(TESTING=True)
    test_client = app.test_client()
    test_client.redis = fake
    return test_client
