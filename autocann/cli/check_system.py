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

import os
import subprocess
import sys
from typing import List, Optional, Tuple

from autocann.config import gpio_pins_from_env, redis_config_from_env
from autocann.hardware.outputs import get_outputs


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


def check_gpio() -> bool:
    try:
        import gpiozero  # noqa: F401
    except Exception as e:
        _warn(f"gpiozero no disponible: {e}", "Normal fuera de la Raspberry Pi")
        return True

    pins = gpio_pins_from_env()
    print(
        f"   Pines configurados — humidificador: BCM {pins.humidity_up}, "
        f"deshumidificador: BCM {pins.humidity_down}, ventilación: BCM {pins.ventilation}"
    )

    duplicates = _duplicate_pins()
    if duplicates:
        return _fail(
            f"Hay pines repetidos entre salidas: {duplicates}",
            "Dos salidas en el mismo pin se encienden y apagan juntas.",
            "Revisá AUTOCANN_PIN_HUMIDITY_UP / _DOWN / _VENTILATION.",
        )

    try:
        import gpiozero

        device = gpiozero.OutputDevice(pins.ventilation, active_high=False, initial_value=False)
        device.close()
        return _ok("GPIO accesible y los pines están libres")
    except Exception as e:
        return _fail(
            f"No se pudo abrir el GPIO: {e}",
            "Si dice 'in use', ya hay un loop de control corriendo: make status",
            "Si es de permisos: sudo usermod -a -G gpio $USER && sudo reboot",
        )


def _duplicate_pins() -> List[Tuple[int, List[str]]]:
    by_pin: dict[int, List[str]] = {}
    for output in get_outputs():
        by_pin.setdefault(int(output["pin_bcm"]), []).append(str(output["name"]))
    return [(pin, names) for pin, names in by_pin.items() if len(names) > 1]


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
    all_ok &= check_gpio()
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
