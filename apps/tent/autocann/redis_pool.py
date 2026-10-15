"""
How the long-running processes connect to Redis: bounded, and on a clock.

Both the backend and the control loop keep one `redis.Redis` for the life of
the process — weeks, under systemd with `Restart=always`. redis-py pools
connections behind that object, and two of its defaults are wrong for a
process that lives that long:

`max_connections` defaults to `2**31`, which is no limit at all. The pool grows
to the highest number of requests that were ever in flight at the same time and
**never shrinks again** — redis-py has no idle reaper. So every burst of
concurrency ratchets the connection count up permanently: a dashboard with a
few tabs open, or a browser retrying a failing poll, walks the pool up a step
at a time and nothing walks it back down. Observed on 2026-10-14: a backend
that had been serving one polling browser for ten hours held 3357 established
connections to Redis, each one idle after having run a single GET. Redis' own
`maxclients` is 10000, so a third of the server was spoken for by one process
that was doing nothing; past that limit Redis starts refusing everybody,
including the control loop that actually drives the tent.

There was no socket timeout either, and that is the half that hurts. Every
Redis call in the control loop is wrapped in `try/except` precisely so that a
Redis which is down cannot stop the loop controlling the tent — but a socket
with no timeout does not raise, it blocks, and an `except` clause never reached
protects nothing. The same silence hung a request thread in the backend, and
with it the dashboard that is supposed to answer "¿anda?".

So: a pool with a hard ceiling, and a socket that gives up. A wedged Redis now
produces an error that the callers' existing `except` clauses handle — the loop
keeps its relays, the dashboard says it cannot reach Redis — instead of a
process that hangs with the tent unsupervised.
"""

from __future__ import annotations

from typing import Optional

import redis

from autocann.config import RedisConfig, redis_config_from_env

#: Hard ceiling on connections per process. The honest demand is small: one or
#: two browsers on the LAN polling a handful of endpoints, plus the ESP32 nodes
#: posting every 30 s. 32 leaves room for a burst without ever being a
#: meaningful share of Redis' 10000 `maxclients`, and it bounds what a runaway
#: client can take from the control loop.
MAX_CONNECTIONS = 32

#: How long a caller waits for a free connection once all 32 are busy. Long
#: enough to ride out a burst, short enough that a wedged pool fails inside a
#: request rather than holding the thread.
POOL_WAIT_SECONDS = 2.0

#: Redis is on localhost (or a container on it). Anything slower than this is
#: not a slow network, it is a Redis that is not answering.
CONNECT_TIMEOUT_SECONDS = 2.0

#: Every command this project sends is a GET, SET or DEL of a small value, so
#: seconds is already generous. This is the timeout that turns "the dashboard
#: hangs forever" into "the dashboard says Redis is not answering".
SOCKET_TIMEOUT_SECONDS = 3.0

#: Connections idle longer than this are PINGed before reuse. Without it, the
#: first command after a Redis restart — or after a night of no traffic — fails
#: on a connection the pool still believes in.
HEALTH_CHECK_INTERVAL_SECONDS = 30


def pooled_client(cfg: Optional[RedisConfig] = None) -> redis.Redis:
    """
    A Redis client for a process that will outlive the day.

    `BlockingConnectionPool` rather than the default pool because the ceiling
    has to mean something: its queue is created with exactly
    `MAX_CONNECTIONS` slots, so the limit is structural. A caller arriving when
    all of them are busy waits `POOL_WAIT_SECONDS` and then gets a
    `ConnectionError`, which is the bounded, visible failure — the default pool
    would simply open connection 33.
    """
    cfg = cfg or redis_config_from_env()
    pool = redis.BlockingConnectionPool(
        host=cfg.host,
        port=cfg.port,
        db=cfg.db,
        max_connections=MAX_CONNECTIONS,
        timeout=POOL_WAIT_SECONDS,
        socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
        socket_timeout=SOCKET_TIMEOUT_SECONDS,
        health_check_interval=HEALTH_CHECK_INTERVAL_SECONDS,
    )
    return redis.Redis(connection_pool=pool)
