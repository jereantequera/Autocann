# Arquitectura - Autocann

Este repo arrancó como un conjunto de scripts y con el tiempo fue creciendo. Para hacerlo mantenible, la lógica se movió a un paquete Python llamado `autocann/` y el directorio `scripts/` quedó como **entrypoints finitos** (wrappers) para correr cosas con `uv run`.

## Principios

- **`autocann/`**: código reusable (db, web, control, hardware). Importable desde cualquier lado.
- **`scripts/`**: solo shell scripts (`.sh`) para iniciar/verificar servicios.
- **Config centralizada**: pines/redis vía `autocann/config.py` + variables de entorno.

## Estructura (alto nivel)

- `autocann/config.py`: configuración (Redis + pines GPIO) con overrides por env vars.
- `autocann/paths.py`: paths del proyecto (`data/`, `autocann/web/templates/`, etc).
- `autocann/time.py`: timezone común.
- `autocann/db.py`: capa de persistencia SQLite + API de “grows”.
- `autocann/web/app.py`: Flask app + endpoints API.
- `autocann/hardware/outputs.py`: definición de outputs (nombre/label/pin/redis_key).
- `autocann/control/vpd_math.py`: funciones puras de cálculo (VPD/targets).

## Entry points

- `python -m autocann.cli.backend` (server web)
- `python -m autocann.cli.vpd` (control VPD)
- `python -m autocann.cli.check_system` (healthcheck)
- `python -m autocann.cli.query_db` (tools de consulta)

## Próximos pasos recomendados

- Migrar gradualmente el loop/control “grande” de `autocann/cli/vpd.py` a `autocann/control/`.

