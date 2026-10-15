#!/usr/bin/env python3
"""
System check for Autocann.

Verifies the dependencies and hardware this project actually uses. It used to
check for two BME280 sensors on I2C, which the code stopped using: the indoor
reading comes from an ESP32 over HTTP (or a local DHT22 on GPIO) and the outdoor
reading from a DHT22. Checking for the wrong hardware reported failures on a
healthy install and passed on a broken one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from autocann.config import (
    DESIRED_OUTPUTS_KEY,
    DESIRED_OUTPUTS_MAX_AGE_SECONDS,
    RELAY_MODE_GPIO,
    RELAY_MODE_REMOTE,
    RELAY_MODES,
    redis_config_from_env,
    relay_mode_from_env,
)
from autocann.hardware.outputs import PIN_SCOPE_REMOTE, pin_sources
from autocann.time import ARGENTINA_TZ


def _ok(message: str) -> bool:
    print(f"✅ {message}")
    return True


def _fail(message: str, *hints: str) -> bool:
    print(f"❌ {message}")
    for hint in hints:
        print(f"   {hint}")
    return False


def _warn(message: str, *hints: str) -> None:
    print(f"⚠️  {message}")
    for hint in hints:
        print(f"   {hint}")


def check_command(command: str, name: str, required: bool = True) -> bool:
    try:
        subprocess.run([command, "--version"], capture_output=True, check=True, timeout=5)
        return _ok(f"{name} está instalado")
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        if not required:
            _warn(f"{name} no está instalado (opcional)")
            return True
        return _fail(f"{name} NO está instalado")


def check_python_package(package_name: str, import_name: str, required: bool = True) -> bool:
    try:
        __import__(import_name)
        return _ok(f"{package_name} está instalado")
    except ImportError:
        if not required:
            _warn(f"{package_name} no está instalado (sólo hace falta en la Raspberry)")
            return True
        return _fail(f"{package_name} NO está instalado", "Instalá con: uv sync --extra rpi")


def check_redis() -> bool:
    cfg = redis_config_from_env()
    try:
        import redis

        redis.Redis(host=cfg.host, port=cfg.port, db=cfg.db, socket_connect_timeout=3).ping()
        return _ok(f"Redis responde en {cfg.host}:{cfg.port}")
    except Exception as e:
        return _fail(
            f"Redis NO responde en {cfg.host}:{cfg.port}: {e}",
            "Arrancalo con: docker start redis-stack-server",
        )


#: Short names for the lines this check prints. Deliberately its own words
#: rather than the dashboard's labels from `get_outputs()`: building those
#: resolves the Raspberry's header, which is the very thing that must not be
#: touched while checking a mode where that header is not in the circuit.
_OUTPUT_WORDS = {
    "humidity_up": "humidificador",
    "humidity_down": "deshumidificador",
    "ventilation": "ventilación",
}

#: Which variables move a pin, per mode. A hint naming the other mode's
#: variables is worse than no hint: it sends you to edit something that is not
#: driving anything.
_PIN_VARS = {
    RELAY_MODE_GPIO: "AUTOCANN_PIN_HUMIDITY_UP / _DOWN / _VENTILATION",
    RELAY_MODE_REMOTE: "AUTOCANN_RELAY_NODE_PIN_HUMIDITY_UP / _DOWN / _VENTILATION",
}


@dataclass(frozen=True)
class DesiredState:
    """What a node polling right now would be served."""

    age_seconds: Optional[int] = None
    outputs: Optional[Dict[str, bool]] = None
    #: None when a node would get real commands. Otherwise what is wrong, in
    #: words, which is also the reason the endpoint would answer failsafe.
    problem: Optional[str] = None


def read_desired_state(raw: Any, now_ts: float) -> DesiredState:
    """
    Read the published desired state exactly as `GET /api/outputs/desired` does.

    A mirror on purpose, and pure so it can be tested without Redis. The check
    is worth something only if it sees what the node sees, so the key, the age
    and the window have to be the same ones the endpoint uses — they are shared
    in `autocann.config` for that reason. Including the detail that an age in
    the future passes: the endpoint only rejects states that are too old, and a
    check stricter than the failsafe would report a fault that does not exist.
    """
    if not raw:
        return DesiredState(problem="el lazo no publicó ningún estado deseado")

    try:
        payload = json.loads(raw)
        outputs = {str(name): bool(on) for name, on in payload["outputs"].items()}
        age = int(now_ts) - int(payload["ts"])
    except Exception as e:
        return DesiredState(problem=f"el estado deseado es ilegible: {e}")

    if age > DESIRED_OUTPUTS_MAX_AGE_SECONDS:
        return DesiredState(
            age_seconds=age,
            outputs=outputs,
            problem=(
                f"el último estado deseado tiene {age}s, más que la ventana de "
                f"{DESIRED_OUTPUTS_MAX_AGE_SECONDS}s"
            ),
        )
    return DesiredState(age_seconds=age, outputs=outputs)


def check_relays() -> bool:
    """
    Check the board that is actually switching the relays.

    This used to open a pin on the Raspberry's header in every mode and report
    "GPIO accesible y los pines están libres". Under AUTOCANN_RELAY_MODE=remote
    — what this installation runs — the header switches nothing: the relays
    hang off an ESP32 that polls `GET /api/outputs/desired`. So the check
    asserted something true and irrelevant about a board outside the circuit,
    stayed green while the node sat in failsafe with every relay dropped, and
    pointed whoever read it at AUTOCANN_PIN_*, variables that in that mode
    drive nothing.
    """
    try:
        mode = relay_mode_from_env()
    except ValueError as e:
        return _fail(
            f"No se pudo leer el modo de relés: {e}",
            f"AUTOCANN_RELAY_MODE tiene que ser uno de: {', '.join(RELAY_MODES)}",
        )

    sources = _report_pins(mode)
    if sources is None:
        return False
    if mode == RELAY_MODE_REMOTE:
        return check_desired_outputs()
    return _check_local_header(sources)


def _report_pins(mode: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """
    Print where each output is really switched, or None if the mapping is unusable.

    `pin_sources` resolves the pins of the board in force and refuses a mapping
    with two outputs on one pin — they would switch together, so the humidifier
    and the dehumidifier could run at once. That refusal is the duplicate-pin
    check, and it is per mode on purpose: in remote mode the Raspberry's
    numbers may collide without any consequence, because nothing is wired to
    them, while the node's numbers carry the whole hazard.
    """
    try:
        sources = pin_sources(mode)
    except ValueError as e:
        _fail(
            f"La asignación de pines no sirve: {e}",
            "Dos salidas en el mismo pin se encienden y apagan juntas.",
            f"Revisá {_PIN_VARS[mode]}.",
        )
        return None

    remote = any(s["scope"] == PIN_SCOPE_REMOTE for s in sources.values())
    where = ", ".join(
        f"{word}: {'GPIO' if remote else 'BCM'} {sources[name]['number']}"
        for name, word in _OUTPUT_WORDS.items()
        if name in sources
    )
    if remote:
        node = next(iter(sources.values()))["node"]
        print(f"   Relés en {node}, no en el header de la Raspberry — {where}")
    else:
        print(f"   Pines del header de la Raspberry — {where}")
    return sources


def check_desired_outputs() -> bool:
    """
    In remote mode, whether a polling node is being given orders.

    The node itself cannot be checked from here, and this says so rather than
    pretending otherwise: it only calls `GET /api/outputs/desired` and reports
    nothing back, so the Raspberry holds no record that it ever polled — the
    same silence that forces its pin mapping to be carried in configuration.
    What is knowable is what any node polling right now would be served, and
    that is what can actually fail on this side.
    """
    cfg = redis_config_from_env()
    try:
        import redis

        raw = redis.Redis(
            host=cfg.host, port=cfg.port, db=cfg.db, socket_connect_timeout=3
        ).get(DESIRED_OUTPUTS_KEY)
    except Exception as e:
        return _fail(
            f"No se pudo leer el estado deseado en Redis: {e}",
            "Sin Redis el nodo no recibe nada y deja los relés apagados.",
        )

    state = read_desired_state(raw, datetime.now(ARGENTINA_TZ).timestamp())
    if state.problem:
        return _fail(
            f"El nodo no está recibiendo órdenes: {state.problem}",
            "Mientras siga así GET /api/outputs/desired contesta failsafe,"
            " o sea todo apagado, y el nodo de-energiza los relés.",
            "El que publica es el loop de control: make status",
            "Si no está corriendo: ./scripts/start_services.sh",
        )

    on = [_OUTPUT_WORDS.get(name, name) for name, value in (state.outputs or {}).items() if value]
    return _ok(
        f"El lazo publica el estado deseado (hace {state.age_seconds}s, ventana de "
        f"{DESIRED_OUTPUTS_MAX_AGE_SECONDS}s) — encendido: "
        f"{', '.join(on) if on else 'nada'}"
    )


def _check_local_header(sources: Dict[str, Dict[str, Any]]) -> bool:
    """In gpio mode the pins are this process's to take, so try to take one."""
    try:
        import gpiozero
    except Exception as e:
        _warn(f"gpiozero no disponible: {e}", "Normal fuera de la Raspberry Pi")
        return True

    try:
        device = gpiozero.OutputDevice(
            int(sources["ventilation"]["number"]), active_high=False, initial_value=False
        )
        device.close()
        return _ok("GPIO accesible y los pines están libres")
    except Exception as e:
        return _fail(
            f"No se pudo abrir el GPIO: {e}",
            "Si dice 'in use', ya hay un loop de control corriendo: make status",
            "Si es de permisos: sudo usermod -a -G gpio $USER && sudo reboot",
        )


def count_control_loops() -> Optional[int]:
    """
    Number of running control loops, or None if it could not be determined.

    Uses `ps` rather than `pgrep -fc`, which is Linux-only: on macOS that flag
    combination is rejected and the empty output silently read as zero.
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,command"], capture_output=True, text=True, timeout=5
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None

    own_pid = str(os.getpid())
    count = 0
    for line in result.stdout.splitlines()[1:]:
        pid, _, command = line.strip().partition(" ")
        # Match the launch form, not the bare module name, so a shell that merely
        # mentions it (a make recipe, this check itself) is not counted.
        if pid == own_pid or "-m autocann.cli.vpd" not in command:
            continue
        # `uv run python -m autocann.cli.vpd` shows up twice: the uv wrapper and
        # the interpreter it spawns. Counting both reports two loops where there
        # is one, which is a false alarm about the exact problem this checks for.
        if command.lstrip().startswith("uv ") or " uv run " in command:
            continue
        count += 1
    return count


def check_no_duplicate_processes() -> bool:
    """Two control loops on the same pins is the classic cause of relay chatter."""
    count = count_control_loops()
    if count is None:
        _warn("No se pudo contar procesos del loop de control")
        return True

    if count > 1:
        return _fail(
            f"Hay {count} loops de control corriendo a la vez",
            "Dos procesos sobre los mismos relés se pelean. Pará todo y arrancá uno:",
            "pkill -f autocann.cli.vpd && ./scripts/start_services.sh",
        )
    return _ok(f"Loops de control corriendo: {count}")


def check_database() -> bool:
    try:
        from autocann.db import ensure_schema, get_active_grow, get_database_stats

        ensure_schema()
        stats = get_database_stats()
        grow = get_active_grow()
    except Exception as e:
        return _fail(f"No se pudo abrir la base de datos: {e}")

    print(f"   {stats.get('sensor_data_count', 0):,} muestras, "
          f"{stats.get('database_size_mb', 0)} MB en {stats.get('database_path')}")
    if not grow:
        return _fail(
            "No hay ningún cultivo activo",
            "El loop de control no hace nada sin cultivo activo.",
            "Creá uno desde el dashboard o con POST /api/grows",
        )
    return _ok(f"Cultivo activo: '{grow['name']}' (etapa {grow['stage']})")


def main() -> int:
    print("=" * 56)
    print("Autocann System Check")
    print("=" * 56)

    all_ok = True

    print("\nComandos del sistema...")
    all_ok &= check_command("uv", "uv")
    all_ok &= check_command("docker", "Docker", required=False)

    print("\nPaquetes Python (base)...")
    all_ok &= check_python_package("flask", "flask")
    all_ok &= check_python_package("redis", "redis")
    all_ok &= check_python_package("pytz", "pytz")

    print("\nPaquetes Python (Raspberry Pi)...")
    all_ok &= check_python_package("gpiozero", "gpiozero", required=False)
    all_ok &= check_python_package("RPi.GPIO", "RPi.GPIO", required=False)
    all_ok &= check_python_package("adafruit-blinka", "board", required=False)
    all_ok &= check_python_package("adafruit-circuitpython-dht", "adafruit_dht", required=False)

    print("\nServicios...")
    all_ok &= check_redis()
    all_ok &= check_database()

    print("\nHardware...")
    all_ok &= check_relays()
    all_ok &= check_no_duplicate_processes()

    print("\n" + "=" * 56)
    if all_ok:
        print("✅ Todo en orden.")
        print("\nPróximo paso:")
        print("  ./scripts/start_services.sh")
        return 0

    print("⚠️  Algunos chequeos fallaron. Mirá las sugerencias de arriba.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
