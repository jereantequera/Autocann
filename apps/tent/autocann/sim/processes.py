"""
Child processes of the simulator: the loop bootstrap, and starting and stopping
exactly the processes the simulator started.
"""

from __future__ import annotations

import signal
import subprocess
import sys
from typing import Any, Mapping, Sequence, Tuple

from autocann.paths import PROJECT_ROOT

#: Sent to the child control loop before it imports anything: hardware modules
#: are blocked, so the loop cannot reach GPIO or a DHT22 of the real tent.
LOOP_BOOTSTRAP = (
    "import sys\n"
    "for _name in ('gpiozero', 'RPi', 'board', 'adafruit_dht'):\n"
    "    sys.modules[_name] = None\n"
    "from autocann.cli.vpd import main\n"
    "raise SystemExit(main(stage_override=None, use_esp32_indoor=True))\n"
)

PR_SET_PDEATHSIG = 1


class SimulatorError(RuntimeError):
    """Something the run could not recover from. The sandbox is torn down."""


def _die_with_parent() -> None:
    """Linux only: the child gets SIGTERM if the simulator dies without cleaning up."""
    try:
        import ctypes

        ctypes.CDLL(None).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except (OSError, AttributeError):
        pass


def start_child(command: Sequence[str], env: Mapping[str, str], stdout: Any = None) -> subprocess.Popen:
    """Start one child and keep its handle. Its own session: a terminal Ctrl-C does not reach it."""
    preexec = _die_with_parent if sys.platform.startswith("linux") else None
    return subprocess.Popen(
        list(command),
        cwd=str(PROJECT_ROOT),
        env=dict(env),
        stdout=stdout,
        stderr=subprocess.STDOUT if stdout is not None else None,
        start_new_session=True,
        preexec_fn=preexec,
    )


def stop_children(children: Sequence[Tuple[str, subprocess.Popen]], grace_seconds: float = 15.0) -> None:
    """
    Stop exactly these processes: SIGTERM, wait, SIGKILL after a timeout. Nothing is found by name.
    """
    for _, proc in children:
        if proc.poll() is None:
            proc.terminate()
    for label, proc in children:
        try:
            proc.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            print(f"{label} did not stop in {grace_seconds:.0f} s; killing pid {proc.pid}")
            proc.kill()
            proc.wait(timeout=5)
