"""
Sandbox guards: the checks that keep the simulator away from the real tent's
Redis DB, port and data, and the environment its child processes get.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Dict, Mapping, Optional

from autocann.config import RELAY_MODE_REMOTE
from autocann.paths import DATA_DIR, PROJECT_ROOT
from autocann.sim.scenarios import indoor_sensor_env

#: Redis DB the sandbox uses unless told otherwise.
DEFAULT_REDIS_DB = 15
#: The real tent's Redis DB. Refused for the sandbox, always.
REAL_TENT_REDIS_DB = 0
#: Redis databases run 0 to 15 by default.
MAX_REDIS_DB = 15

#: Ports the real tent's backend uses (5000, and 5055 on the same Pi). Refused.
RESERVED_PORTS = frozenset({5000, 5055})


class SandboxError(ValueError):
    """A setting that would let the simulator reach the real tent."""


def check_redis_db(db: int) -> int:
    if db == REAL_TENT_REDIS_DB:
        raise SandboxError(
            "refusing Redis DB 0: that is the real tent's. The simulator uses "
            f"DB {DEFAULT_REDIS_DB} by default"
        )
    if not 1 <= db <= MAX_REDIS_DB:
        raise SandboxError(f"Redis DB {db} is out of range (1 to {MAX_REDIS_DB})")
    return db


def check_port(port: int) -> int:
    if port in RESERVED_PORTS:
        raise SandboxError(f"refusing port {port}: the real tent's backend uses it")
    if not 1024 <= port <= 65535:
        raise SandboxError(f"port {port} is out of range")
    return port


def check_db_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    data_dir = DATA_DIR.resolve()
    if data_dir == resolved or data_dir in resolved.parents:
        raise SandboxError(f"refusing {resolved}: it is inside the real data directory")
    if resolved.exists():
        raise SandboxError(f"{resolved} already exists; the simulator needs a fresh file")
    return resolved


def free_port() -> int:
    """A port the OS says is free on 127.0.0.1, never a reserved one."""
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if port not in RESERVED_PORTS:
            return port


def build_child_env(
    *,
    redis_db: int,
    db_path: Path,
    port: int,
    scenario: str,
    base: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """
    Environment for the backend and the control loop.

    The relay mode is forced to remote, the Redis DB and SQLite file are the
    sandbox's own, and indoor sensor roles come from the scenario alone.
    """
    env = dict(os.environ if base is None else base)
    env.pop("PORT", None)
    for key in ("AUTOCANN_INDOOR_PRIMARY", "AUTOCANN_INDOOR_WITNESS", "AUTOCANN_INDOOR_OBSERVERS"):
        env.pop(key, None)
    env.update({
        "AUTOCANN_REDIS_DB": str(check_redis_db(redis_db)),
        "AUTOCANN_DB": str(db_path),
        "AUTOCANN_RELAY_MODE": RELAY_MODE_REMOTE,
        "AUTOCANN_WEB_HOST": "127.0.0.1",
        "AUTOCANN_WEB_PORT": str(check_port(port)),
        "AUTOCANN_WEB_DEBUG": "0",
        "PYTHONPATH": str(PROJECT_ROOT),
        "PYTHONUNBUFFERED": "1",
    })
    env.update(indoor_sensor_env(scenario))
    return env
