#!/usr/bin/env python
"""
Compact a database bloated by the old per-iteration control logging.

The old control loop wrote a `control_events` row on every iteration while an
output was energised, not just when it changed. Production ended up with
2,734,795 rows describing about 7,100 actual transitions — 385 times more noise
than signal, and essentially the whole 174 MB of the file.

This **compacts rather than purges**: every transition is kept, every repetition
of a state already recorded is dropped. The relay history survives intact and is
still usable as the baseline to compare the new loop against; what goes is the
redundancy.

`sensor_data` is never touched. That history is irreplaceable.

Dry run by default. `--apply` makes a backup first, then compacts and VACUUMs.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Rows whose value repeats the previous row for the same event_type. The first
# row of each type is always kept, so the series still has a starting state.
_REDUNDANT_IDS = """
    SELECT id FROM (
        SELECT id, value,
               LAG(value) OVER (PARTITION BY event_type ORDER BY timestamp, id) AS prev
        FROM control_events
    )
    WHERE prev IS NOT NULL AND value = prev
"""


def _human(n: float) -> str:
    return f"{n / 1024 / 1024:,.1f} MB"


def analyse(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM control_events").fetchone()[0]
    if total == 0:
        return {"total": 0, "redundant": 0, "keep": 0}
    redundant = conn.execute(
        f"SELECT COUNT(*) FROM ({_REDUNDANT_IDS})").fetchone()[0]
    return {"total": total, "redundant": redundant, "keep": total - redundant}


def report(path: Path, stats: dict) -> None:
    size = path.stat().st_size
    print(f"\nArchivo:            {path}  ({_human(size)})")
    if stats["total"] == 0:
        print("control_events:     vacío, no hay nada que compactar")
        return

    pct = stats["redundant"] / stats["total"] * 100
    print(f"control_events:     {stats['total']:>12,} filas")
    print(f"  transiciones:     {stats['keep']:>12,}  ← se conservan")
    print(f"  repeticiones:     {stats['redundant']:>12,}  ← se eliminan ({pct:.2f}%)")


def compact(path: Path, backup: bool = True) -> int:
    """Compact in place. Returns the number of rows removed."""
    if backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = path.with_name(f"{path.stem}.antes-de-compactar-{stamp}{path.suffix}")
        print(f"\n💾 Copia de seguridad → {destination.name}")
        # The backup API gives a consistent copy even if something is writing.
        source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        target = sqlite3.connect(destination)
        source.backup(target)
        target.close()
        source.close()
        print(f"   {_human(destination.stat().st_size)}")

    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")

    print("\n🗜️  Eliminando repeticiones...")
    started = time.monotonic()
    cursor = conn.execute(f"DELETE FROM control_events WHERE id IN ({_REDUNDANT_IDS})")
    removed = cursor.rowcount
    conn.commit()
    print(f"   {removed:,} filas eliminadas en {time.monotonic() - started:.1f}s")

    before = path.stat().st_size
    print("\n🧹 VACUUM (recupera el espacio en disco)...")
    started = time.monotonic()
    # VACUUM cannot run inside a transaction.
    conn.isolation_level = None
    conn.execute("VACUUM")
    conn.close()
    after = path.stat().st_size
    print(f"   {_human(before)} → {_human(after)}  "
          f"({(1 - after / before) * 100:.0f}% menos) en {time.monotonic() - started:.1f}s")

    return removed


def purge_redis(host: str, port: int) -> None:
    """
    Drop the dead historical_* series.

    The averaging in store_historical_data() never fired, so these grew without
    bound: in production historical_data_1w alone held 94,892 points and 9.3 MB
    where a working average leaves about 7. The new code neither writes nor
    reads them.
    """
    try:
        import redis
    except ImportError:
        print("\n⚠️  redis no está instalado, salteando la limpieza de Redis")
        return

    try:
        client = redis.Redis(host=host, port=port, socket_connect_timeout=5)
        client.ping()
    except Exception as e:
        print(f"\n⚠️  Redis no responde en {host}:{port}: {e}")
        return

    keys = [k for k in client.keys("historical_*")]
    if not keys:
        print("\n✅ Redis: no hay claves historical_* que borrar")
        return

    total = sum(client.strlen(k) for k in keys)
    print(f"\n🧹 Redis: borrando {len(keys)} claves historical_* ({_human(total)})")
    client.delete(*keys)
    print(f"   memoria usada ahora: {client.info('memory')['used_memory_human']}")


def _services_running() -> Optional[str]:
    """Name of a running Autocann service, if any — compacting under one is unsafe."""
    import subprocess

    try:
        out = subprocess.run(["ps", "-eo", "command"], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if "-m autocann.cli.vpd" in line or "-m autocann.cli.backend" in line:
            return line.strip()[:60]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("database", type=Path, help="Base a compactar")
    parser.add_argument("--apply", action="store_true",
                        help="Ejecutar de verdad (sin esto sólo informa)")
    parser.add_argument("--no-backup", action="store_true",
                        help="No hacer copia de seguridad antes (no recomendado)")
    parser.add_argument("--redis", action="store_true",
                        help="Borrar también las claves historical_* de Redis")
    parser.add_argument("--redis-host", default="localhost")
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--force", action="store_true",
                        help="Compactar aunque haya servicios corriendo")
    args = parser.parse_args()

    if not args.database.exists():
        print(f"❌ No existe: {args.database}")
        return 1

    try:
        conn = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
        stats = analyse(conn)
        samples = conn.execute("SELECT COUNT(*) FROM sensor_data").fetchone()[0]
        conn.close()
    except sqlite3.Error as e:
        print(f"❌ No se pudo leer la base: {e}")
        return 1

    print("=" * 66)
    print("  Compactación de control_events")
    print("=" * 66)
    report(args.database, stats)
    print(f"\nsensor_data:        {samples:>12,} filas  ← NO se toca")

    if stats["redundant"] == 0:
        print("\n✅ Nada que compactar.")
        if args.redis and args.apply:
            purge_redis(args.redis_host, args.redis_port)
        return 0

    if not args.apply:
        print("\n" + "-" * 66)
        print("Esto fue una simulación. Para ejecutarlo:")
        print(f"  python -m autocann.cli.compact_db {args.database} --apply")
        print("\nHace una copia de seguridad antes de tocar nada.")
        return 0

    running = _services_running()
    if running and not args.force:
        print(f"\n❌ Hay un servicio corriendo:\n   {running}")
        print("\nPará los servicios antes de compactar: una escritura a mitad del")
        print("VACUUM puede dejar la base inconsistente.")
        print("  pkill -f '[a]utocann.cli.vpd'; pkill -f '[a]utocann.cli.backend'")
        print("\nO pasá --force si sabés lo que estás haciendo.")
        return 1

    removed = compact(args.database, backup=not args.no_backup)

    conn = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    after = analyse(conn)
    samples_after = conn.execute("SELECT COUNT(*) FROM sensor_data").fetchone()[0]
    conn.close()

    print(f"\n🔎 integrity_check: {integrity}")
    print(f"   control_events:  {after['total']:,} filas (quedaban {stats['keep']:,})")
    print(f"   sensor_data:     {samples_after:,} filas (había {samples:,})")

    if integrity != "ok" or samples_after != samples or after["total"] != stats["keep"]:
        print("\n❌ Algo no cuadra. Restaurá la copia de seguridad.")
        return 1

    if args.redis:
        purge_redis(args.redis_host, args.redis_port)

    print(f"\n✅ Listo. {removed:,} filas redundantes eliminadas, "
          f"{after['total']:,} transiciones conservadas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
