#!/usr/bin/env python
"""
Report on a database brought back from production.

Answers the questions the roadmap's validation step asks, over real history
instead of the synthetic data everything was developed against:

- how much history is there, and what shape is it in
- how often were the relays switching (the baseline the short-cycle protection
  has to beat)
- how unreliable is the sensor, as a number rather than an impression
- what does the VPD score look like when it is not computed from data designed
  to produce a nice score

Opens the file **read-only** and never migrates it. Point it at a copy pulled
with `make pull-data`, not at a live production database.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

from autocann.time import ARGENTINA_TZ


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt_ts(ts: Optional[int]) -> str:
    if ts is None:
        return "--"
    return datetime.fromtimestamp(ts, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M")


def _heading(title: str) -> None:
    print(f"\n{'=' * 64}\n  {title}\n{'=' * 64}")


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _tables(conn: sqlite3.Connection) -> set:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def report_overview(conn: sqlite3.Connection, path: Path) -> None:
    _heading("Resumen")
    size_mb = round(path.stat().st_size / (1024 * 1024), 2)
    samples = conn.execute("SELECT COUNT(*) FROM sensor_data").fetchone()[0]
    first, last = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM sensor_data").fetchone()

    print(f"Archivo:          {path}  ({size_mb} MB)")
    print(f"Muestras:         {samples:,}")
    print(f"Rango:            {_fmt_ts(first)}  →  {_fmt_ts(last)}")

    if first and last and samples:
        days = max((last - first) / 86400, 1)
        print(f"Cobertura:        {days:.1f} días, {samples / days:,.0f} muestras/día")
        # The loop writes every 5 minutes, so 288/day is the ceiling.
        print(f"Completitud:      {min(samples / days / 288 * 100, 100):.0f}% "
              f"(288/día = sin huecos)")

    grows = conn.execute(
        "SELECT id, name, stage, start_date, is_active FROM grows ORDER BY id"
    ).fetchall()
    print(f"\nCultivos ({len(grows)}):")
    for g in grows:
        marca = "◀ activo" if g["is_active"] else ""
        n = conn.execute(
            "SELECT COUNT(*) FROM sensor_data WHERE grow_id = ?", (g["id"],)
        ).fetchone()[0]
        print(f"  [{g['id']}] {g['name'][:34]:34} {g['stage']:11} "
              f"{str(g['start_date'])[:10]}  {n:>7,} muestras {marca}")


def report_gaps(conn: sqlite3.Connection) -> None:
    """Where the system was down, which synthetic data never shows."""
    _heading("Huecos en el histórico")
    rows = conn.execute("SELECT timestamp FROM sensor_data ORDER BY timestamp").fetchall()
    if len(rows) < 2:
        print("Muy pocas muestras para evaluar.")
        return

    gaps = []
    for prev, cur in zip(rows, rows[1:]):
        delta = cur["timestamp"] - prev["timestamp"]
        if delta > 900:        # 3 missed samples
            gaps.append((prev["timestamp"], delta))

    if not gaps:
        print("Sin interrupciones de más de 15 minutos. 👌")
        return

    total = sum(d for _, d in gaps)
    print(f"{len(gaps)} interrupciones de más de 15 min, {total / 3600:.1f} h en total.")
    print("\nLas 10 más largas:")
    for ts, delta in sorted(gaps, key=lambda g: -g[1])[:10]:
        horas = delta / 3600
        print(f"  {_fmt_ts(ts)}  →  {horas:6.1f} h sin datos")


def report_control_events(conn: sqlite3.Connection) -> None:
    """
    How often the relays were switching.

    This is the baseline: the short-cycle protection should show up as a drop in
    this number once the new loop has run for a few days.
    """
    _heading("Actividad de los relés (línea de base)")
    if "control_events" not in _tables(conn):
        print("No hay tabla control_events.")
        return

    total = conn.execute("SELECT COUNT(*) FROM control_events").fetchone()[0]
    if total == 0:
        print("Sin eventos registrados.")
        return

    first, last = conn.execute(
        "SELECT MIN(timestamp), MAX(timestamp) FROM control_events"
    ).fetchone()
    days = max((last - first) / 86400, 1)

    print(f"Eventos totales:  {total:,} en {days:.1f} días")
    print(f"Por día:          {total / days:,.0f}")
    print("\n⚠️  Ojo al leer esto: el loop viejo registraba un evento en cada")
    print("    iteración mientras una salida estaba activa, no sólo en los")
    print("    cambios. Así que este número mezcla 'cuánto conmutó' con")
    print("    'cuánto tiempo estuvo encendido'. La comparación justa es contra")
    print("    los días que corra el loop nuevo, que sólo registra transiciones.")

    print("\nPor tipo:")
    for row in conn.execute(
        "SELECT event_type, value, COUNT(*) n FROM control_events"
        " GROUP BY event_type, value ORDER BY n DESC"
    ):
        print(f"  {row['event_type']:16} {row['value']:4} {row['n']:>8,}")


def report_quality(conn: sqlite3.Connection) -> None:
    """How unreliable the sensor actually is — the number that decides the swap."""
    _heading("Calidad de las lecturas")
    cols = _columns(conn, "sensor_data")

    if "quality" not in cols or not conn.execute(
        "SELECT COUNT(*) FROM sensor_data WHERE quality IS NOT NULL"
    ).fetchone()[0]:
        print("Este histórico es anterior a la fase 2, así que no registra")
        print("calidad por muestra. El dato va a aparecer recién después de")
        print("unos días con el loop nuevo corriendo — y es el que debería")
        print("decidir si cambiar el DHT22 es urgente o no.")
    else:
        counts = Counter(
            r["quality"] for r in conn.execute(
                "SELECT quality FROM sensor_data WHERE quality IS NOT NULL")
        )
        total = sum(counts.values())
        for quality, n in counts.most_common():
            print(f"  {quality:12} {n:>8,}  {n / total * 100:5.1f}%")

    if "sample_n" in cols:
        row = conn.execute(
            "SELECT AVG(sample_n) a, MIN(sample_n) lo, MAX(sample_n) hi"
            " FROM sensor_data WHERE sample_n IS NOT NULL"
        ).fetchone()
        if row and row["a"]:
            print(f"\nLecturas por muestra: promedio {row['a']:.0f} "
                  f"(min {row['lo']}, max {row['hi']})")

    # Stuck readings are the classic sign of a sensor on its way out.
    repeated = conn.execute(
        "SELECT temperature, COUNT(*) n FROM sensor_data"
        " GROUP BY temperature ORDER BY n DESC LIMIT 3"
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM sensor_data").fetchone()[0]
    if repeated and total:
        print("\nValores de temperatura más repetidos:")
        for row in repeated:
            print(f"  {row['temperature']}°C  ×{row['n']:,}  ({row['n'] / total * 100:.1f}%)")


def report_conditions(conn: sqlite3.Connection) -> None:
    _heading("Condiciones reales")
    row = conn.execute(
        """
        SELECT AVG(temperature) t, MIN(temperature) tlo, MAX(temperature) thi,
               AVG(humidity) h, MIN(humidity) hlo, MAX(humidity) hhi,
               AVG(vpd) v, AVG(leaf_vpd) lv
        FROM sensor_data
        """
    ).fetchone()
    if not row or row["t"] is None:
        print("Sin datos.")
        return

    print(f"Temperatura:  {row['t']:5.1f} °C   (de {row['tlo']:.1f} a {row['thi']:.1f})")
    print(f"Humedad:      {row['h']:5.1f} %    (de {row['hlo']:.1f} a {row['hhi']:.1f})")
    print(f"VPD aire:     {row['v']:5.2f} kPa")
    if row["lv"] is not None:
        print(f"VPD hoja:     {row['lv']:5.2f} kPa")

    print("\nTiempo en rango por etapa (sobre VPD de hoja, como controla el loop):")
    from autocann.control.vpd_math import VPD_RANGES

    for stage, (lo, hi) in VPD_RANGES.items():
        row = conn.execute(
            """
            SELECT COUNT(*) total,
                   SUM(CASE WHEN COALESCE(leaf_vpd, vpd) BETWEEN ? AND ? THEN 1 ELSE 0 END) ok
            FROM sensor_data
            """,
            (lo, hi),
        ).fetchone()
        if row["total"]:
            pct = (row["ok"] or 0) / row["total"] * 100
            print(f"  {stage:11} {lo}–{hi} kPa  →  {pct:5.1f}% del histórico completo")

    print("\n(Es el histórico entero contra cada banda, no el score por etapa:")
    print(" para eso hacen falta los stage_events, que se crean al migrar.)")


def report_schema(conn: sqlite3.Connection) -> None:
    _heading("Estado del esquema")
    tables = _tables(conn)
    cols = _columns(conn, "sensor_data")

    checks = [
        ("stage_events (fase 1)", "stage_events" in tables),
        ("sensor_calibration (fase 2)", "sensor_calibration" in tables),
        ("min/max por muestra (fase 2)", "temperature_min" in cols),
        ("contexto por muestra (fase 2)", "stage" in cols),
        ("exterior nullable", _outdoor_nullable(conn)),
    ]
    for label, present in checks:
        print(f"  {'✅' if present else '⬜'} {label}")

    pending = [label for label, present in checks if not present]
    if pending:
        print("\nLas migraciones pendientes corren solas la primera vez que")
        print("arranque el código nuevo. Son idempotentes y están testeadas,")
        print("pero conviene probarlas primero sobre esta copia:")
        print("\n  AUTOCANN_DB=<esta copia> uv run python -c \\")
        print("    'import autocann.db as db; db.ensure_schema()'")


def _outdoor_nullable(conn: sqlite3.Connection) -> bool:
    for row in conn.execute("PRAGMA table_info(sensor_data)"):
        if row[1] == "outside_temperature":
            return row[3] == 0
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("database", type=Path, help="Copia de la base de producción")
    args = parser.parse_args()

    if not args.database.exists():
        print(f"❌ No existe: {args.database}")
        return 1

    try:
        conn = _connect(args.database)
    except sqlite3.Error as e:
        print(f"❌ No se pudo abrir: {e}")
        return 1

    print(f"\n🔎 Inspección de {args.database} (solo lectura)")
    try:
        report_overview(conn, args.database)
        report_conditions(conn)
        report_gaps(conn)
        report_control_events(conn)
        report_quality(conn)
        report_schema(conn)
    finally:
        conn.close()

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
