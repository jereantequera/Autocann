"""
Tent simulator: the whole stack, with fake sensors and relays.

Starts a sandboxed backend and control loop, then drives them with a fake
sensor node (posting like firmware/sensor_node) and a fake relay node (polling
like firmware/relay_node) against a small model of the tent. Run it with
`make sim` from apps/tent.

Isolation is the point: the machine this runs on may be driving a real tent.
Every child process gets a non-zero Redis DB, a fresh SQLite file, remote relay
mode (never GPIO) and a free 127.0.0.1 port, and only the PIDs this module
started are ever signalled.

This module keeps the command line and the run. The guards, the tent model,
the scenarios, the fake nodes and the child processes live in autocann/sim/,
and their names are re-exported here for the tests and for callers.
"""

from __future__ import annotations

import argparse
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from autocann.config import redis_config_from_env
from autocann.hardware.outputs import OUTPUT_NAMES
from autocann.sim.db_lock import DbLock, claim_redis_db, clear_db_keeping_lock
from autocann.sim.fake_nodes import (
    RELAY_POLL_SECONDS,
    RELAY_WATCHDOG_SECONDS,
    FakeRelayNode,
    FakeSensorNode,
    TentWorld,
    apply_desired,
)
from autocann.sim.http_client import (
    http_json,
)
from autocann.sim.model import (
    BASE_RH_TAU_SECONDS,
    BASE_TEMP_TAU_SECONDS,
    DEHUMIDIFIER_RH_PER_SECOND,
    HUMIDIFIER_RH_PER_SECOND,
    OUTDOOR_RH,
    OUTDOOR_TEMP_C,
    VENTILATION_TAU_SECONDS,
    TentModel,
    _clamp_rh,
    _relax,
    status_line,
)
from autocann.sim.nodes import Reading, build_sensor_payload
from autocann.sim.processes import (
    LOOP_BOOTSTRAP,
    PR_SET_PDEATHSIG,
    SimulatorError,
    _die_with_parent,
    start_child,
    stop_children,
)
from autocann.sim.sandbox import (
    DEFAULT_REDIS_DB,
    MAX_REDIS_DB,
    REAL_TENT_REDIS_DB,
    RESERVED_PORTS,
    SandboxError,
    build_child_env,
    check_db_path,
    check_port,
    check_redis_db,
    free_port,
)
from autocann.sim.scenarios import (
    DRIFT_MAX_C,
    DRIFT_RATE_C_PER_HOUR,
    OBSERVER_RH_OFFSET,
    OBSERVER_SENSOR_ID,
    OBSERVER_TEMP_OFFSET_C,
    PRIMARY_SENSOR_ID,
    SCENARIOS,
    SENSOR_NOISE,
    SPIKE_AT_SECONDS,
    SPIKE_TEMP_DELTA_C,
    STALE_AFTER_SECONDS,
    Scenario,
    _jitter,
    indoor_sensor_env,
    initial_model,
    relay_polls,
    scenario_readings,
    scenario_spec,
    witness_offset_c,
)
from autocann.sim.seed import seed_sandbox_history

# Names the tests and callers take from this module. The run itself lives here.
__all__ = [
    "OUTPUT_NAMES",
    "Reading",
    "build_sensor_payload",
    "OUTDOOR_TEMP_C",
    "OUTDOOR_RH",
    "BASE_TEMP_TAU_SECONDS",
    "BASE_RH_TAU_SECONDS",
    "VENTILATION_TAU_SECONDS",
    "HUMIDIFIER_RH_PER_SECOND",
    "DEHUMIDIFIER_RH_PER_SECOND",
    "_relax",
    "_clamp_rh",
    "TentModel",
    "status_line",
    "SENSOR_NOISE",
    "OBSERVER_TEMP_OFFSET_C",
    "OBSERVER_RH_OFFSET",
    "DRIFT_RATE_C_PER_HOUR",
    "DRIFT_MAX_C",
    "STALE_AFTER_SECONDS",
    "SPIKE_AT_SECONDS",
    "SPIKE_TEMP_DELTA_C",
    "PRIMARY_SENSOR_ID",
    "OBSERVER_SENSOR_ID",
    "Scenario",
    "SCENARIOS",
    "initial_model",
    "scenario_spec",
    "witness_offset_c",
    "_jitter",
    "scenario_readings",
    "indoor_sensor_env",
    "relay_polls",
    "DEFAULT_REDIS_DB",
    "REAL_TENT_REDIS_DB",
    "MAX_REDIS_DB",
    "RESERVED_PORTS",
    "SandboxError",
    "check_redis_db",
    "check_port",
    "check_db_path",
    "free_port",
    "build_child_env",
    "LOOP_BOOTSTRAP",
    "PR_SET_PDEATHSIG",
    "SimulatorError",
    "_die_with_parent",
    "start_child",
    "stop_children",
    "http_json",
    "RELAY_POLL_SECONDS",
    "RELAY_WATCHDOG_SECONDS",
    "apply_desired",
    "TentWorld",
    "FakeSensorNode",
    "FakeRelayNode",
    "main",
    "build_parser",
    "run",
]


DEFAULT_INTERVAL_SECONDS = 5.0
REPORT_EVERY_SECONDS = 10.0
TICK_SECONDS = 0.5


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def _wait_for_backend(base_url: str, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SimulatorError(f"backend exited with code {proc.returncode}; see its log")
        try:
            http_json("GET", f"{base_url}/api/config", timeout=2.0)
            return
        except (OSError, ValueError):
            time.sleep(0.5)
    raise SimulatorError("backend did not answer in time")


def _create_grow(base_url: str, scenario: str) -> None:
    result = http_json("POST", f"{base_url}/api/grows",
                       {"name": f"Simulated grow ({scenario})", "stage": "early_veg"})
    if not result.get("success"):
        raise SimulatorError(f"could not create the simulated grow: {result}")


def _reset_redis(lock: DbLock) -> None:
    """Empty the sandbox's own Redis DB (keeping its lock), so no key from an earlier run can pause or fail-safe this one."""
    clear_db_keeping_lock(lock.client)


def run(args: argparse.Namespace) -> int:
    explicit_db = args.redis_db is not None
    scenario = args.scenario
    scenario_spec(scenario)
    # Validated before anything is created, so a refused path leaves nothing behind.
    requested_db = check_db_path(Path(args.db)) if args.db else None

    stop = threading.Event()
    children: List[Tuple[str, subprocess.Popen]] = []
    threads: List[threading.Thread] = []
    lock: Optional[DbLock] = None
    log_file = None
    previous_handlers = {}
    workdir = Path(tempfile.mkdtemp(prefix="autocann-sim-"))

    def request_stop(signum, _frame) -> None:
        print(f"\nSignal {signum} received, stopping the simulator...")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[sig] = signal.signal(sig, request_stop)

    exit_code = 0
    try:
        db_path = requested_db or workdir / "autocann-sim.db"
        port = check_port(free_port())
        base_url = f"http://127.0.0.1:{port}"
        # The DB lock is taken before anything is flushed. A DB another live simulator holds is refused.
        lock = claim_redis_db(requested=args.redis_db if explicit_db else DEFAULT_REDIS_DB,
                              explicit=explicit_db, cfg=redis_config_from_env())
        redis_db = lock.db
        if redis_db != DEFAULT_REDIS_DB:
            print(f"Using Redis DB {redis_db}")
        env = build_child_env(redis_db=redis_db, db_path=db_path, port=port, scenario=scenario)

        _reset_redis(lock)
        print(f"Scenario: {scenario} - {SCENARIOS[scenario].description}")
        print(f"Sandbox: Redis DB {redis_db}, SQLite {db_path}, relay mode remote, port {port}")

        log_file = open(workdir / "backend.log", "w", encoding="utf-8")
        backend = start_child([sys.executable, "-m", "autocann.cli.backend"], env, stdout=log_file)
        children.append(("backend", backend))
        _wait_for_backend(base_url, backend)
        _create_grow(base_url, scenario)
        if args.history:
            try:
                print(seed_sandbox_history(db_path, args.history, with_gaps=args.history_gaps,
                                           now=int(time.time()), sandbox_dir=None if requested_db else workdir))
            except ValueError as e:
                raise SimulatorError(f"could not seed the history: {e}") from None

        loop = start_child([sys.executable, "-c", LOOP_BOOTSTRAP], env)
        children.append(("control loop", loop))

        world = TentWorld(scenario)
        sensor_node = FakeSensorNode(base_url, scenario, world, args.interval, stop, random.Random())
        threads.append(sensor_node)
        if relay_polls(scenario):
            threads.append(FakeRelayNode(base_url, world, stop))
        for thread in threads:
            thread.start()

        print(f"Dashboard: {base_url}/  (open it while the simulator runs)")
        print("Press Ctrl-C to stop.")
        _run_until_done(stop, children, world, args)
    except (SimulatorError, OSError) as e:
        print(f"Simulator error: {e}", file=sys.stderr)
        exit_code = 1
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=5)
        stop_children(children)
        if lock is not None:
            lock.release()
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        if log_file is not None:
            log_file.close()
        shutil.rmtree(workdir, ignore_errors=True)
        print("Simulator stopped; its processes and listener are gone.")
    return exit_code


def _run_until_done(stop: threading.Event, children: Sequence[Tuple[str, subprocess.Popen]],
                    world: TentWorld, args: argparse.Namespace) -> None:
    started = time.monotonic()
    last_tick = started
    last_report = started - REPORT_EVERY_SECONDS
    while not stop.is_set():
        now = time.monotonic()
        world.advance(now - last_tick, args.speed)
        last_tick = now
        if args.duration is not None and now - started >= args.duration:
            print("Duration reached.")
            return
        for label, proc in children:
            if proc.poll() is not None:
                raise SimulatorError(f"{label} exited with code {proc.returncode}")
        if now - last_report >= REPORT_EVERY_SECONDS:
            last_report = now
            print(status_line(now - started, world.snapshot(), world.outputs()), flush=True)
        stop.wait(TICK_SECONDS)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _scenario_help() -> str:
    lines = ["scenarios:"]
    for spec in SCENARIOS.values():
        lines.append(f"  {spec.name:<14} {spec.description}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m autocann.cli.simulate",
        description="Run the backend, the control loop and fake sensor and relay nodes "
                    "in an isolated sandbox.",
        epilog=_scenario_help(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--scenario", choices=list(SCENARIOS), default="normal",
                        help="what to simulate (default: normal)")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="model acceleration: model seconds per wall second (default: 1)")
    parser.add_argument("--duration", type=float, default=None,
                        help="stop after this many wall seconds (default: until Ctrl-C)")
    parser.add_argument("--redis-db", type=int, default=None,
                        help=f"Redis DB for the sandbox; 0 is refused. Default: the first free "
                             f"DB from {DEFAULT_REDIS_DB} down to 1")
    parser.add_argument("--db", default=None,
                        help="SQLite file to create; must not exist (default: a temp file)")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS,
                        help="seconds between sensor posts (default: 5)")
    parser.add_argument("--history", type=int, default=0, metavar="DAYS",
                        help="seed DAYS of synthetic history into the grow first (default: 0)")
    parser.add_argument("--history-gaps", action="store_true",
                        help="with --history, drop two 2-hour holes from it")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.speed <= 0:
        parser.error("--speed must be positive")
    if args.duration is not None and args.duration <= 0:
        parser.error("--duration must be positive")
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if args.history < 0:
        parser.error("--history must not be negative")
    if args.history_gaps and not args.history:
        parser.error("--history-gaps needs --history")
    if args.redis_db is not None:
        try:
            check_redis_db(args.redis_db)
        except SandboxError as e:
            parser.error(str(e))
    try:
        return run(args)
    except SandboxError as e:
        parser.error(str(e))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
