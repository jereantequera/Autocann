#!/bin/bash
#
# Arranca Redis, el backend web y el loop de control.
#
# Es idempotente: si un servicio ya está corriendo no arranca otro. Antes el
# guard del backend buscaba "python backend.py", un proceso que ya no existe,
# así que cada ejecución levantaba un backend más y todos peleaban por el
# puerto 5000. El loop de control no tenía guard, y dos loops sobre los mismos
# pines GPIO terminan en GPIOPinInUse o en relés golpeando.

set -uo pipefail

PROJECT_DIR="${AUTOCANN_DIR:-/home/autocann/Autocann}"
FECHA=$(date +'%Y-%m-%d')
# Corchetes a propósito: el patrón matchea "autocann" pero el texto escrito acá
# dice "[a]utocann", así que pgrep no se matchea a sí mismo ni a este script.
VPD_PATTERN='[a]utocann\.cli\.vpd'
BACKEND_PATTERN='[a]utocann\.cli\.backend' 

export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

cd "$PROJECT_DIR" || { echo "❌ No existe $PROJECT_DIR"; exit 1; }

if ! command -v uv > /dev/null 2>&1; then
    echo "❌ uv no está instalado o no está en PATH"
    echo "   Instalalo con: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

mkdir -p logs

# Redis es opcional para que el loop arranque, pero sin él no hay dashboard.
if command -v docker > /dev/null 2>&1; then
    docker start redis-stack-server > /dev/null 2>&1 \
        || echo "⚠️  No se pudo arrancar redis-stack-server (¿existe el contenedor?)"
else
    echo "⚠️  docker no encontrado, salteando Redis"
fi

# Configuración de esta instalación. Ver README.
#
#   INDOOR_PRIMARY / INDOOR_WITNESS: los dos sensores del nodo interior. El
#   primario controla; el testigo sólo lo vigila por deriva. Sin esto el lazo
#   lee la clave antigua `esp32_indoor`, que ya nadie escribe desde que el
#   firmware postea con sensor_id — y se queda en failsafe para siempre.
#
#   RELAY_MODE=remote: los relés los maneja el nodo ESP32, no el GPIO de la Pi.
#
# Se exporta ANTES de arrancar el backend, no sólo antes del lazo: el backend
# también lee los ids, para saber qué sensores existen y cuál controla cuando
# el dashboard pide /api/sensors/indoor. Exportándolo después, el backend
# quedaba con la instalación de un solo sensor y el testigo no aparecía en
# pantalla.
export AUTOCANN_INDOOR_PRIMARY="${AUTOCANN_INDOOR_PRIMARY:-a}"
export AUTOCANN_INDOOR_WITNESS="${AUTOCANN_INDOOR_WITNESS:-b}"
export AUTOCANN_RELAY_MODE="${AUTOCANN_RELAY_MODE:-remote}"

if pgrep -f "$BACKEND_PATTERN" > /dev/null 2>&1; then
    echo "ℹ️  Backend ya está corriendo"
else
    echo "▶️  Iniciando backend..."
    uv run python -m autocann.cli.backend >> "logs/backend_$FECHA.log" 2>&1 &
fi

if pgrep -f "$VPD_PATTERN" > /dev/null 2>&1; then
    echo "ℹ️  Loop de control ya está corriendo — no arranco otro"
    exit 0
fi

# Sin argumento de etapa: usa la del cultivo activo en la base de datos.
echo "▶️  Iniciando loop de control de VPD..."
echo "    sensores: primario=$AUTOCANN_INDOOR_PRIMARY testigo=$AUTOCANN_INDOOR_WITNESS · relés: $AUTOCANN_RELAY_MODE"
exec uv run python -u -m autocann.cli.vpd >> "logs/vpd_$FECHA.log" 2>&1
