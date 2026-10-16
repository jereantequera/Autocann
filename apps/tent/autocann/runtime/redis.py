"""
The control loop's Redis client, and the best-effort write the loop and its readers use.

Modules import `redis_client` by name, so a test patches the module that uses it.
"""

from __future__ import annotations

from typing import Optional

from autocann.config import redis_config_from_env
from autocann.redis_pool import pooled_client

#: Expiry on the Redis keys the dashboard reads, so a dead loop stops looking alive.
REDIS_KEY_TTL_SECONDS = 600


_redis_cfg = redis_config_from_env()


#: Bounded pool, and a socket with a timeout so that the `try/except` around
#: every read and write below can actually fire. See autocann/redis_pool.py.
redis_client = pooled_client(_redis_cfg)


def _set_redis(key: str, value: str, ttl: Optional[int] = REDIS_KEY_TTL_SECONDS) -> None:
    """Best-effort Redis write. Redis being down must not stop the control loop."""
    try:
        redis_client.set(key, value, ex=ttl)
    except Exception as e:
        print(f"⚠️ Redis write failed for '{key}': {e}")
