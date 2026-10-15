"""
The pool has a ceiling and the socket has a clock.

A backend that had been serving one polling browser for ten hours was found
holding 3357 established connections to Redis, all idle. Nothing was leaking
them one request at a time: redis-py's pool grows to the highest concurrency it
has ever seen and never shrinks, and with the default `max_connections` of
`2**31` there is nothing to stop it. These tests are about the construction
rather than live behaviour — the point is that nobody quietly goes back to a
bare `redis.Redis(host=...)`, which is what the leak was.
"""

from __future__ import annotations

import os
import threading

import pytest
import redis

from autocann.config import RedisConfig
from autocann.redis_pool import (
    CONNECT_TIMEOUT_SECONDS,
    MAX_CONNECTIONS,
    POOL_WAIT_SECONDS,
    SOCKET_TIMEOUT_SECONDS,
    pooled_client,
)

CFG = RedisConfig(host="localhost", port=6379, db=0)


def pool_of(client):
    return client.connection_pool


# ---------------------------------------------------------------------------
# How the client is built
# ---------------------------------------------------------------------------


def test_the_pool_has_a_ceiling():
    """The whole bug in one assertion: an unbounded pool is not a pool."""
    pool = pool_of(pooled_client(CFG))
    assert pool.max_connections == MAX_CONNECTIONS
    assert pool.max_connections < 2**31


def test_the_ceiling_is_structural_not_advisory():
    """
    `BlockingConnectionPool` creates its queue with exactly that many slots,
    so the limit cannot be exceeded by a caller that simply asks again.
    """
    pool = pool_of(pooled_client(CFG))
    assert isinstance(pool, redis.BlockingConnectionPool)
    assert pool.pool.maxsize == MAX_CONNECTIONS
    assert pool.timeout == POOL_WAIT_SECONDS


def test_the_socket_gives_up():
    """
    Without these two, a wedged Redis does not raise, it blocks — and every
    `except` clause written to survive a Redis that is down protects nothing.
    """
    kwargs = pool_of(pooled_client(CFG)).connection_kwargs
    assert kwargs["socket_connect_timeout"] == CONNECT_TIMEOUT_SECONDS
    assert kwargs["socket_timeout"] == SOCKET_TIMEOUT_SECONDS


def test_idle_connections_are_checked_before_reuse():
    """A connection idle across a Redis restart is PINGed, not trusted."""
    kwargs = pool_of(pooled_client(CFG)).connection_kwargs
    assert kwargs["health_check_interval"] > 0


def test_the_config_is_carried_through():
    cfg = RedisConfig(host="redis.example", port=6380, db=3)
    kwargs = pool_of(pooled_client(cfg)).connection_kwargs
    assert (kwargs["host"], kwargs["port"], kwargs["db"]) == ("redis.example", 6380, 3)


# ---------------------------------------------------------------------------
# That the ceiling holds, without a live Redis
# ---------------------------------------------------------------------------


class StubConnection:
    """Enough of a redis-py Connection to be handed out by the pool."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        # The pool checks this to decide whether a released connection is its
        # own (`owns_connection`), so the stub has to carry it too.
        self.pid = os.getpid()

    def connect(self):
        pass

    def can_read(self, timeout=0):
        return False

    def disconnect(self, *args):
        pass

    def register_connect_callback(self, _):
        pass

    def deregister_connect_callback(self, _):
        pass


@pytest.fixture
def stub_pool():
    """The real pool, handing out connections that never touch a socket."""
    pool = pool_of(pooled_client(CFG))
    pool.connection_class = StubConnection
    pool.reset()
    return pool


def test_the_pool_stops_at_the_ceiling(stub_pool):
    held = [stub_pool.get_connection() for _ in range(MAX_CONNECTIONS)]
    assert len(held) == MAX_CONNECTIONS

    # The 33rd caller waits POOL_WAIT_SECONDS and is then refused, rather than
    # being handed a brand new connection the way the default pool would.
    with pytest.raises(redis.ConnectionError):
        stub_pool.get_connection()

    stub_pool.release(held[0])
    assert stub_pool.get_connection() is not None


def test_connections_are_reused_rather_than_accumulated(stub_pool):
    """
    Sequential use must not grow the pool — this is the shape of the leak.
    """
    for _ in range(MAX_CONNECTIONS * 4):
        stub_pool.release(stub_pool.get_connection())
    assert len(stub_pool._connections) == 1


def test_concurrent_use_cannot_exceed_the_ceiling(stub_pool):
    """
    The leak was a high-water mark of concurrency that was never given back.
    It may reach the ceiling; it may not pass it.
    """
    errors = []

    def use():
        try:
            conn = stub_pool.get_connection()
            try:
                pass
            finally:
                stub_pool.release(conn)
        except Exception as e:  # pragma: no cover - only on a regression
            errors.append(e)

    threads = [threading.Thread(target=use) for _ in range(MAX_CONNECTIONS * 3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(stub_pool._connections) <= MAX_CONNECTIONS


# ---------------------------------------------------------------------------
# That the two long-lived processes actually use it
# ---------------------------------------------------------------------------


def test_the_backend_builds_its_client_through_the_factory(monkeypatch):
    import autocann.web.app as web

    built = []
    monkeypatch.setattr(web, "pooled_client", lambda *a, **k: built.append(a) or object())
    web.create_app()
    assert built, "create_app() must go through pooled_client()"


def test_the_control_loop_client_is_pooled():
    import autocann.cli.vpd as vpd

    pool = vpd.redis_client.connection_pool
    assert isinstance(pool, redis.BlockingConnectionPool)
    assert pool.max_connections == MAX_CONNECTIONS
    assert pool.connection_kwargs["socket_timeout"] == SOCKET_TIMEOUT_SECONDS
