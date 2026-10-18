"""
Redis DB lock for the simulator: one running simulator per DB.

Two simulators on the same DB write the same keys and flush each other's
state, so each run takes a lock on its DB before it flushes anything. The lock
is the key ``autocann:sim:lock`` holding the simulator's pid, with a TTL that a
background thread keeps extending while the run lives. A lock whose pid is no
longer alive is stale and can be taken over.

The DB choice is a pure function (``pick_redis_db``) so it can be tested with
a fake lookup; ``claim_redis_db`` is the one place that talks to Redis.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Optional

from autocann.config import RedisConfig
from autocann.sim.sandbox import MAX_REDIS_DB, SandboxError, check_redis_db

logger = logging.getLogger(__name__)

LOCK_KEY = "autocann:sim:lock"
#: The lock expires this long after the last refresh if the run dies without
#: releasing it. The refresher runs well inside this window.
LOCK_TTL_SECONDS = 30
REFRESH_SECONDS = 10.0


def pid_alive(pid: int) -> bool:
    """True when a process with this pid exists (signal 0 checks, sends nothing)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def _parse_pid(raw) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def live_holder(raw) -> Optional[int]:
    """The pid holding a lock value, or None when the lock is free or stale."""
    pid = _parse_pid(raw)
    if pid is None or not pid_alive(pid):
        return None
    return pid


def pick_redis_db(
    requested: int,
    explicit: bool,
    is_locked: Callable[[int], bool],
    holder_pid: Optional[Callable[[int], Optional[int]]] = None,
) -> int:
    """
    The DB this run should use.

    An explicit request is used if it is free and refused otherwise. A default
    request takes the first free DB going down from 15 to 1. DB 0 is never
    returned: ``check_redis_db`` refuses it.
    """
    if explicit:
        check_redis_db(requested)
        if is_locked(requested):
            raise SandboxError(_in_use(requested, holder_pid))
        return requested
    for db in range(MAX_REDIS_DB, 0, -1):
        if not is_locked(db):
            return db
    raise SandboxError(
        f"all Redis DBs 1 to {MAX_REDIS_DB} are in use by other simulators; "
        "stop one of them first"
    )


def _in_use(db: int, holder_pid: Optional[Callable[[int], Optional[int]]]) -> str:
    pid = holder_pid(db) if holder_pid else None
    who = f" (pid {pid})" if pid else ""
    return (f"Redis DB {db} is in use by another simulator{who}; stop it, "
            "or leave out --redis-db to use a free DB")


class DbLock:
    """The lock on one Redis DB, held by this process."""

    def __init__(self, client, db: int, pid: int) -> None:
        self.client = client
        self.db = db
        self.pid = pid
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _holds(self) -> bool:
        return _parse_pid(self.client.get(LOCK_KEY)) == self.pid

    def acquire(self) -> bool:
        """Take the lock, or take it over from a stale holder. False if a live one has it."""
        value = str(self.pid)
        if self.client.set(LOCK_KEY, value, nx=True, ex=LOCK_TTL_SECONDS):
            return True
        if live_holder(self.client.get(LOCK_KEY)) is not None:
            return False
        self.client.delete(LOCK_KEY)  # stale: its pid is gone
        return bool(self.client.set(LOCK_KEY, value, nx=True, ex=LOCK_TTL_SECONDS))

    def refresh(self) -> bool:
        """Extend the TTL while we still hold the lock. False if we lost it."""
        if not self._holds():
            return False
        return bool(self.client.expire(LOCK_KEY, LOCK_TTL_SECONDS))

    def start_refresher(self) -> None:
        def loop() -> None:
            while not self._stop.wait(REFRESH_SECONDS):
                try:
                    if not self.refresh():
                        logger.warning("simulator lock on Redis DB %s was lost", self.db)
                except Exception as e:  # Redis hiccup: keep trying until released
                    logger.warning("could not refresh the simulator lock: %s", e)

        self._thread = threading.Thread(target=loop, name="sim-db-lock", daemon=True)
        self._thread.start()

    def release(self) -> None:
        """Stop refreshing and delete the key, but only if it still holds our pid."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._holds():
            self.client.delete(LOCK_KEY)


def clear_db_keeping_lock(client) -> None:
    """
    Delete every key in the DB except the simulator lock.

    The lock lives in the DB it guards, so a plain FLUSHDB would drop it and
    leave the run unlocked.
    """
    keys = []
    for key in client.scan_iter(count=500):
        text = key.decode("utf-8", errors="replace") if isinstance(key, bytes) else key
        if text != LOCK_KEY:
            keys.append(key)
        if len(keys) >= 500:
            client.delete(*keys)
            keys = []
    if keys:
        client.delete(*keys)


def claim_redis_db(
    *,
    requested: int,
    explicit: bool,
    cfg: RedisConfig,
    pid: Optional[int] = None,
) -> DbLock:
    """
    Pick a DB, take its lock and start refreshing it. Call before flushing.

    Raises SandboxError when the DB is in use by a live simulator.
    """
    import redis

    owner = os.getpid() if pid is None else pid

    def client(db: int):
        return redis.Redis(host=cfg.host, port=cfg.port, db=db, socket_timeout=3)

    def holder(db: int) -> Optional[int]:
        return live_holder(client(db).get(LOCK_KEY))

    db = pick_redis_db(requested, explicit, lambda d: holder(d) is not None, holder_pid=holder)
    lock = DbLock(client(db), db, owner)
    if not lock.acquire():
        raise SandboxError(_in_use(db, holder))
    lock.start_refresher()
    return lock
