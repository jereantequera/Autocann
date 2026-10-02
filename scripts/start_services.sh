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
exec uv run python -u -m autocann.cli.vpd >> "logs/vpd_$FECHA.log" 2>&1
