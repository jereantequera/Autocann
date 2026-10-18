"""The simulator's Redis DB lock: picking a DB, taking, refreshing and releasing the lock."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

import autocann.sim.db_lock as db_lock
from autocann.config import RedisConfig
from autocann.sim.db_lock import (
    LOCK_KEY,
    LOCK_TTL_SECONDS,
    DbLock,
    claim_redis_db,
    clear_db_keeping_lock,
    pick_redis_db,
)
from autocann.sim.sandbox import SandboxError


class FakeRedisDb:
    """The calls the lock makes, with NX and TTLs recorded."""

    def __init__(self):
        self.store = {}
        self.ttls = {}

    def get(self, key):
        value = self.store.get(key)
        return value.encode() if isinstance(value, str) else value

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        self.ttls[key] = ex
        return True

    def expire(self, key, seconds):
        if key not in self.store:
            return False
        self.ttls[key] = seconds
        return True

    def delete(self, *keys):
        removed = 0
        for key in keys:
            removed += key in self.store
            self.store.pop(key, None)
            self.ttls.pop(key, None)
        return removed

    def scan_iter(self, count=None):
        return iter(list(self.store))


def dead_pid() -> int:
    """The pid of a process that has already exited."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def locked_by(db: FakeRedisDb, pid: int) -> None:
    db.set(LOCK_KEY, pid, ex=LOCK_TTL_SECONDS)


@pytest.fixture
def fakes(monkeypatch):
    """One fake per Redis DB, handed out by a stand-in for redis.Redis."""
    import redis

    dbs = {}

    def fake_redis(host, port, db, socket_timeout=None):
        return dbs.setdefault(db, FakeRedisDb())

    monkeypatch.setattr(redis, "Redis", fake_redis)
    return dbs


# ---------------------------------------------------------------------------
# Picking the DB
# ---------------------------------------------------------------------------


def test_default_request_takes_15_when_free():
    assert pick_redis_db(15, False, lambda db: False) == 15


def test_default_request_with_15_locked_takes_14():
    assert pick_redis_db(15, False, lambda db: db == 15) == 14


def test_default_request_walks_down_past_several_locks():
    locked = {15, 14, 13}
    assert pick_redis_db(15, False, lambda db: db in locked) == 12


def test_default_request_with_every_db_locked_is_an_error():
    with pytest.raises(SandboxError, match="all Redis DBs 1 to 15"):
        pick_redis_db(15, False, lambda db: True)


def test_default_request_never_picks_db_zero():
    picked = []

    def locked(db):
        picked.append(db)
        return db != 1  # only DB 1 is free

    assert pick_redis_db(15, False, locked) == 1
    assert 0 not in picked


def test_explicit_locked_db_is_refused_and_names_the_pid():
    with pytest.raises(SandboxError, match="DB 15 is in use by another simulator \\(pid 4242\\)"):
        pick_redis_db(15, True, lambda db: db == 15, holder_pid=lambda db: 4242)


def test_explicit_free_db_is_used():
    assert pick_redis_db(7, True, lambda db: False) == 7


def test_explicit_db_zero_is_refused_even_when_free():
    with pytest.raises(SandboxError, match="DB 0"):
        pick_redis_db(0, True, lambda db: False)


# ---------------------------------------------------------------------------
# Taking, stale locks, refreshing, releasing
# ---------------------------------------------------------------------------


def test_acquire_sets_our_pid_with_a_ttl():
    db = FakeRedisDb()
    lock = DbLock(db, 15, os.getpid())
    assert lock.acquire()
    assert db.store[LOCK_KEY] == str(os.getpid())
    assert db.ttls[LOCK_KEY] == LOCK_TTL_SECONDS


def test_live_holder_blocks_acquire():
    db = FakeRedisDb()
    locked_by(db, os.getppid())  # a live process that is not us
    assert not DbLock(db, 15, os.getpid()).acquire()
    assert db.store[LOCK_KEY] == str(os.getppid())


def test_stale_lock_with_a_dead_pid_is_taken_over():
    db = FakeRedisDb()
    locked_by(db, dead_pid())
    lock = DbLock(db, 15, os.getpid())
    assert lock.acquire()
    assert db.store[LOCK_KEY] == str(os.getpid())


def test_garbage_lock_value_counts_as_stale():
    db = FakeRedisDb()
    db.set(LOCK_KEY, "not a pid", ex=LOCK_TTL_SECONDS)
    assert DbLock(db, 15, os.getpid()).acquire()


def test_release_deletes_only_our_own_key():
    db = FakeRedisDb()
    lock = DbLock(db, 15, os.getpid())
    lock.acquire()
    lock.release()
    assert LOCK_KEY not in db.store


def test_release_leaves_a_key_now_held_by_someone_else():
    db = FakeRedisDb()
    lock = DbLock(db, 15, os.getpid())
    lock.acquire()
    locked_by(db, os.getppid())  # someone took it over after ours expired
    lock.release()
    assert db.store[LOCK_KEY] == str(os.getppid())


def test_refresh_extends_the_ttl():
    db = FakeRedisDb()
    lock = DbLock(db, 15, os.getpid())
    lock.acquire()
    db.ttls[LOCK_KEY] = 3  # about to expire
    assert lock.refresh()
    assert db.ttls[LOCK_KEY] == LOCK_TTL_SECONDS


def test_refresh_does_nothing_when_the_lock_is_not_ours():
    db = FakeRedisDb()
    lock = DbLock(db, 15, os.getpid())
    locked_by(db, os.getppid())
    db.ttls[LOCK_KEY] = 3
    assert not lock.refresh()
    assert db.ttls[LOCK_KEY] == 3


def test_refresher_thread_refreshes_while_running(monkeypatch):
    monkeypatch.setattr(db_lock, "REFRESH_SECONDS", 0.01)
    db = FakeRedisDb()
    lock = DbLock(db, 15, os.getpid())
    lock.acquire()
    db.ttls[LOCK_KEY] = 1
    lock.start_refresher()
    try:
        for _ in range(200):
            if db.ttls[LOCK_KEY] == LOCK_TTL_SECONDS:
                break
            time.sleep(0.01)
        assert db.ttls[LOCK_KEY] == LOCK_TTL_SECONDS
    finally:
        lock.release()
    assert LOCK_KEY not in db.store


# ---------------------------------------------------------------------------
# claim_redis_db: picker and lock together, on fake Redis DBs
# ---------------------------------------------------------------------------

CFG = RedisConfig(host="localhost", port=6379, db=0)


def test_second_default_claim_gets_14_while_the_first_holds_15(fakes):
    first = claim_redis_db(requested=15, explicit=False, cfg=CFG)
    second = claim_redis_db(requested=15, explicit=False, cfg=CFG)
    try:
        assert (first.db, second.db) == (15, 14)
    finally:
        second.release()
        first.release()


def test_explicit_claim_on_a_live_lock_is_refused(fakes):
    holder = claim_redis_db(requested=15, explicit=True, cfg=CFG)
    try:
        with pytest.raises(SandboxError, match=f"pid {os.getpid()}"):
            claim_redis_db(requested=15, explicit=True, cfg=CFG)
    finally:
        holder.release()


def test_claim_after_release_gets_the_db_back(fakes):
    lock = claim_redis_db(requested=15, explicit=True, cfg=CFG)
    lock.release()
    again = claim_redis_db(requested=15, explicit=True, cfg=CFG)
    try:
        assert again.db == 15
    finally:
        again.release()


def test_flush_keeps_the_lock_and_drops_everything_else():
    db = FakeRedisDb()
    lock = DbLock(db, 15, os.getpid())
    lock.acquire()
    db.set("autocann:grow:1", "x")
    db.set("sensor:indoor", "y")
    clear_db_keeping_lock(db)
    assert set(db.store) == {LOCK_KEY}
    assert lock.refresh()
