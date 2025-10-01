#!/bin/bash

FECHA=$(date +'%Y-%m-%d')

# Add uv to PATH (installed in user's .cargo/bin or .local/bin)
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

# Iniciar el contenedor de Redis (usando la ruta completa a docker y evitando error en caso de fallo)
/usr/bin/docker start redis-stack-server || true

cd /home/autocann/Autocann

# Crear directorio de logs si no existe
mkdir -p logs

cd scripts

# Verificar si uv está disponible
if ! command -v uv &> /dev/null; then
    echo "Error: uv no está instalado o no se encuentra en PATH"
    echo "Instalá uv con: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Verificar si el backend ya está corriendo
if ! pgrep -f "python backend.py" > /dev/null; then
    echo "Iniciando backend..."
    # Iniciar el backend en segundo plano usando uv
    uv run backend.py > "/home/autocann/Autocann/logs/backend_$FECHA.log" 2>&1 &
else
    echo "Backend ya está corriendo"
fi

# Iniciar el script de VPD usando uv (con output unbuffered para logs en tiempo real)
# No pasamos argumento para que use el stage del cultivo activo en la base de datos
uv run python -u fix-vpd.py >> "/home/autocann/Autocann/logs/vpd_$FECHA.log" 2>&1