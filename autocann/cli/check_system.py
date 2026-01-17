#!/usr/bin/env python3
"""
System check script for Autocann project.
Verifies all dependencies and hardware connections.
"""

from __future__ import annotations

import subprocess
import sys


def check_command(command: str, name: str) -> bool:
    """Check if a command is available."""
    try:
        subprocess.run([command, "--version"], capture_output=True, check=True, timeout=5)
        print(f"✅ {name} is installed")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        print(f"❌ {name} is NOT installed")
        return False


def check_python_package(package_name: str, import_name: str | None = None) -> bool:
    """Check if a Python package is available."""
    if import_name is None:
        import_name = package_name

    try:
        __import__(import_name)
        print(f"✅ {package_name} is installed")
        return True
    except ImportError:
        print(f"❌ {package_name} is NOT installed")
        return False


def check_i2c() -> bool:
    """Check if I2C is enabled and devices are detected."""
    try:
        result = subprocess.run(["i2cdetect", "-y", "1"], capture_output=True, text=True, timeout=5)

        if "76" in result.stdout and "77" in result.stdout:
            print("✅ Both BME280 sensors detected (0x76 and 0x77)")
            return True
        if "76" in result.stdout or "77" in result.stdout:
            print("⚠️  Only one BME280 sensor detected")
            print("   Expected: 0x76 (indoor) and 0x77 (outdoor)")
            return False

        print("❌ No BME280 sensors detected on I2C bus")
        print("   Run 'sudo raspi-config' and enable I2C")
        return False
    except FileNotFoundError:
        print("❌ i2cdetect command not found")
        print("   Install with: sudo apt-get install i2c-tools")
        return False
    except subprocess.TimeoutExpired:
        print("❌ I2C detection timed out")
        return False


def check_redis() -> bool:
    """Check if Redis is running."""
    try:
        import redis

        client = redis.Redis(host="localhost", port=6379, db=0)
        client.ping()
        print("✅ Redis is running and accessible")
        return True
    except Exception as e:
        print(f"❌ Redis is NOT accessible: {e}")
        print("   Start with: docker start redis-stack-server")
        return False


def check_gpio() -> bool:
    """Check if GPIO is accessible."""
    try:
        import gpiozero  # noqa: F401

        print("✅ GPIO library is accessible")
        return True
    except Exception as e:
        print(f"❌ GPIO is NOT accessible: {e}")
        print("   You may need to run as root or add user to gpio group")
        return False


def main() -> int:
    print("=" * 50)
    print("Autocann System Check")
    print("=" * 50)
    print()

    all_ok = True

    print("Checking system commands...")
    all_ok &= check_command("docker", "Docker")
    all_ok &= check_command("uv", "uv")
    print()

    print("Checking Python packages...")
    all_ok &= check_python_package("flask", "flask")
    all_ok &= check_python_package("redis", "redis")
    all_ok &= check_python_package("pytz", "pytz")
    all_ok &= check_python_package("gpiozero", "gpiozero")
    all_ok &= check_python_package("adafruit-blinka", "board")
    all_ok &= check_python_package("adafruit-circuitpython-bme280", "adafruit_bme280")
    all_ok &= check_python_package("RPi.GPIO", "RPi.GPIO")
    print()

    print("Checking hardware connections...")
    all_ok &= check_i2c()
    print()

    print("Checking services...")
    all_ok &= check_redis()
    all_ok &= check_gpio()
    print()

    print("=" * 50)
    if all_ok:
        print("✅ All checks passed! System is ready.")
        print()
        print("Next steps:")
        print("  ./scripts/start_services.sh")
        print("  or")
        print("  make run-vpd")
        return 0

    print("⚠️  Some checks failed. Please fix the issues above.")
    print()
    print("Common solutions:")
    print("  - Install dependencies: uv sync")
    print("  - Enable I2C: sudo raspi-config")
    print("  - Start Redis: docker start redis-stack-server")
    print("  - Add user to gpio group: sudo usermod -a -G gpio $USER")
    return 1


if __name__ == "__main__":
    sys.exit(main())

