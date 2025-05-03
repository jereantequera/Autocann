#!/bin/bash

FECHA=$(date +'%Y-%m-%d')

# Iniciar el contenedor de Redis (usando la ruta completa a docker y evitando error en caso de fallo)
/usr/bin/docker start redis-stack-server || true

cd /home/autocann/Autocann/scripts

# Verificar si el backend ya está corriendo
if ! pgrep -f "python backend.py" > /dev/null; then
    echo "Iniciando backend..."
    # Iniciar el backend en segundo plano
    /home/autocann/Autocann/venv/bin/python backend.py > "/home/autocann/Autocann/logs/backend_$FECHA.log" 2>&1 &
else
    echo "Backend ya está corriendo"
fi

# Iniciar el script de VPD
/home/autocann/Autocann/venv/bin/python fix-vpd.py early_veg 2>> "/home/autocann/Autocann/logs/errors_vpd_$FECHA.log"