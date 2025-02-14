#!/bin/bash

FECHA=$(date +'%Y-%m-%d')

# Iniciar el contenedor de Redis (usando la ruta completa a docker y evitando error en caso de fallo)
/usr/bin/docker start redis-stack-server || true

cd /home/autocann/Autocann/scripts

/home/autocann/Autocann/venv/bin/python fix-vpd.py early_veg 2>> "/home/autocann/Autocann/logs/errors_vpd_$FECHA.log"