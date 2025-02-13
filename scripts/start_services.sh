#!/bin/bash

FECHA=$(date +'%Y-%m-%d')
# Iniciar el contenedor de Redis
docker start redis-stack
cd /home/autocann/Autocann
source autocann/bin/activate
cd /home/autocann/Autocann/scripts
python fix-vpd.py early_veg 2>> "/home/autocann/Autocann/logs/errors_vpd_$FECHA.log"

