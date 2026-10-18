"""
Redis connection settings, read from the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RedisConfig:
    host: str = "localhost"
    port: int = 6379
    db: int = 0


def redis_config_from_env() -> RedisConfig:
    return RedisConfig(
        host=os.getenv("AUTOCANN_REDIS_HOST", "localhost"),
        port=int(os.getenv("AUTOCANN_REDIS_PORT", "6379")),
        db=int(os.getenv("AUTOCANN_REDIS_DB", "0")),
    )
