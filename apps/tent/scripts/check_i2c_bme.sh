#!/bin/bash
#
# Chequea sensores I2C en el bus 1.
#
# Nota: el proyecto hoy usa DHT22 (1-Wire sobre GPIO) y/o un ESP32 que postea
# por HTTP. Este script sólo sirve si volvés a sensores I2C tipo BME280/SHT3x,
# que es justamente lo que recomienda README.md para evitar los problemas de
# lectura del DHT22.

set -uo pipefail

BUS="${I2C_BUS:-1}"
EXPECTED=("${@:-76 77}")

if ! command -v i2cdetect > /dev/null 2>&1; then
    echo "❌ i2cdetect no encontrado. Instalalo con: sudo apt-get install i2c-tools"
    exit 1
fi

FOUND=$(i2cdetect -y "$BUS" | tail -n +2 | cut -d: -f2 | tr -s ' ' '\n' | grep -E '^[0-9a-f]{2}$')

OK=0
for addr in ${EXPECTED[*]}; do
    if echo "$FOUND" | grep -qx "$addr"; then
        echo "✅ Dispositivo I2C detectado en 0x$addr"
    else
        echo "❌ Nada en 0x$addr"
        OK=1
    fi
done

exit $OK
