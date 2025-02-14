#!/bin/bash

FECHA=$(date +'%Y-%m-%d')

# Iniciar el contenedor de Redis (usando la ruta completa a docker y evitando error en caso de fallo)
 /usr/bin/docker start redis-stack || true

cd /home/autocann/Autocann/scripts

# Ejecutar Python desde el entorno virtual sin necesidad de "source"
./venv/bin/python fix-vpd.py early_veg 2>> "/home/autocann/Autocann/logs/errors_vpd_$FECHA.log"