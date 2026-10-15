# Autocann

Control de clima para cultivos (cualquier planta) y la plataforma que recibe los
datos de los equipos instalados en casa de los clientes.

Este repo es un monorepo: todo vive acá, cada parte con su propio tooling.

```
apps/tent/          La carpa de pruebas: Python + Flask + Redis en una Raspberry Pi,
                    con sus nodos ESP32 (firmware Arduino). Es lo que hoy controla
                    una carpa real. Ver apps/tent/README.md.
crates/             Workspace de Rust: la plataforma.
  protocol/         El contrato equipo ↔ plataforma: tópicos, mensajes, perfiles
                    de cultivo, VPD. Sin I/O.
  mqtt/             Conexión al broker. Siempre mTLS.
  ingest/           autocann-ingest: recibe la telemetría y la valida.
  device-sim/       autocann-device-sim: un equipo de mentira, para probar sin hardware.
deploy/
  pki/              CA de desarrollo (dev-pki.sh).
  mosquitto/        Broker de desarrollo con mTLS y ACL por equipo.
docs/
  platform.md       El diseño de la plataforma, y por qué.
```

## Empezar

Hace falta [uv](https://docs.astral.sh/uv/) para la carpa y
[rustup](https://rustup.rs/) para Rust (la versión la fija
`rust-toolchain.toml`).

```bash
make test          # tests de la carpa y de Rust
make lint          # ruff, clippy y rustfmt
make help          # todo lo demás
```

### La plataforma en local, de punta a punta

Cuatro terminales, desde la raíz del repo:

```bash
make pki-init && make pki-device DEVICE=carpa-01   # una vez
make dev-broker                                    # Mosquitto con mTLS en :8883
make dev-ingest                                    # el ingestor
make dev-sim DEVICE=carpa-01                       # un equipo publicando
```

El ingestor muestra cada lectura con su VPD. Un equipo sin certificado no
conecta, y uno con el certificado de otro no puede publicar en su nombre.

### La carpa

Todo lo de la carpa sigue funcionando desde `apps/tent/`, con los mismos
comandos de siempre. Desde la raíz, cualquier target se alcanza con el prefijo
`tent-`: `make tent-deploy`, `make tent-logs`, `make tent-check`.

## Migrar una instalación existente de la carpa

Antes del monorepo, la carpa vivía en la raíz del repo. `data/`, `logs/` y
`.venv/` no están en git, así que no se mueven solos:

```bash
cd /home/autocann/Autocann
sudo systemctl stop fix-vpd               # nada escribiendo la base mientras se mueve
git pull
mv data logs apps/tent/
rm -rf .venv && (cd apps/tent && uv sync --extra rpi)
```

Después hay que apuntar los servicios a la carpeta nueva:
`WorkingDirectory=/home/autocann/Autocann/apps/tent` en el unit de systemd (ver
`apps/tent/HARDWARE.md`), o `AUTOCANN_DIR` si se usa `start_services.sh`. Recién
entonces, `sudo systemctl daemon-reload && sudo systemctl start fix-vpd`.
