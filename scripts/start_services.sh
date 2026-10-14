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
#   INDOOR_PRIMARY / INDOOR_WITNESS / INDOOR_OBSERVERS: los sensores del nodo
#   interior. El primario controla. El testigo sólo lo vigila por deriva, y para
#   eso tiene que estar en el mismo aire. Los observadores no controlan ni se
#   cruzan: se leen y se muestran. Sin INDOOR_PRIMARY el lazo lee la clave
#   antigua `esp32_indoor`, que ya nadie escribe desde que el firmware postea
#   con sensor_id — y se queda en failsafe para siempre.
#
#   En esta carpa `b` está ENCIMA de la lámpara, a propósito: interesa saber la
#   temperatura ahí arriba. Eso lo descalifica como testigo — el offset contra
#   `a` se abre y se cierra con el fotoperíodo, así que el detector mediría la
#   luz, no la salud del sensor. Va como observador, y la instalación queda
#   SIN detección de deriva hasta que haya un tercer DHT22 al lado de `a`.
#
#   RELAY_MODE=remote: los relés los maneja el nodo ESP32, no el GPIO de la Pi.
#   RELAY_NODE_NAME: con qué nombre aparece ese nodo en el dashboard. El panel
#   muestra el pin de cada salida, y en remoto ese pin es un GPIO del ESP32:
#   sin saber de qué placa es, el número manda a medir el header equivocado.
#   Los pines (25/26/27) ya salen del default, que copia al firmware.
#
# Se exporta ANTES de arrancar el backend, no sólo antes del lazo: el backend
# también lee los ids, para saber qué sensores existen y cuál controla cuando
# el dashboard pide /api/sensors/indoor. Exportándolo después, el backend
# quedaba con la instalación de un solo sensor y el testigo no aparecía en
# pantalla.
export AUTOCANN_INDOOR_PRIMARY="${AUTOCANN_INDOOR_PRIMARY:-a}"
export AUTOCANN_INDOOR_WITNESS="${AUTOCANN_INDOOR_WITNESS:-}"
export AUTOCANN_INDOOR_OBSERVERS="${AUTOCANN_INDOOR_OBSERVERS:-b}"
export AUTOCANN_RELAY_MODE="${AUTOCANN_RELAY_MODE:-remote}"
export AUTOCANN_RELAY_NODE_NAME="${AUTOCANN_RELAY_NODE_NAME:-ESP32 relés (192.168.100.77)}"

#   AUTOCANN_PIN: el PIN del tablero. VACÍO POR DEFECTO, a propósito: sin él
#   nada pide nada y todo se comporta igual que siempre. Un deploy que se olvida
#   la variable no puede dejar afuera a nadie de su propio tablero — no ver la
#   carpa es peor falla que ser legible desde la LAN en la que ya estás.
#
#   Con un valor, el tablero pide el PIN una vez por navegador y la sesión dura
#   30 días (cookie firmada). Se usa desde el celular parado en la carpa: pedirlo
#   todos los días es cómo termina escrito al lado de la puerta.
#
#   ⚠️  Lo que el PIN NO cubre, y no puede cubrir, son los dos nodos ESP32:
#   `GET /api/outputs/desired`, `POST /api/sensor/indoor` y `POST /api/node/log`.
#   El firmware se graba a mano, con un cable USB, parado en la carpa: no puede
#   mandar una cookie porque el servidor empezó a pedirla. Y el primero es el
#   peor: el nodo de relés lo pide cada 2 s y **se desenergiza solo a los 60 s
#   sin un poll exitoso**. Un 401 ahí abre todos los relés de la carpa un minuto
#   después. Están exentos en autocann/web/auth.py, con el motivo escrito al
#   lado. No los "arregles".
export AUTOCANN_PIN="${AUTOCANN_PIN:-}"

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
echo "    sensores: primario=$AUTOCANN_INDOOR_PRIMARY testigo=${AUTOCANN_INDOOR_WITNESS:-(ninguno)} observadores=${AUTOCANN_INDOOR_OBSERVERS:-(ninguno)} · relés: $AUTOCANN_RELAY_MODE ($AUTOCANN_RELAY_NODE_NAME)"
exec uv run python -u -m autocann.cli.vpd >> "logs/vpd_$FECHA.log" 2>&1
