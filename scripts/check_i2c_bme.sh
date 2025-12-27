#!/bin/bash

BUS=1
EXPECTED=("76" "77")

FOUND=$(i2cdetect -y $BUS | tr -s ' ' | grep -oE '[0-9a-f]{2}')

OK=1
for addr in "${EXPECTED[@]}"; do
  if ! echo "$FOUND" | grep -q "$addr"; then
    echo "❌ BME280 no detectado en 0x$addr"
    OK=0
  else
    echo "✅ BME280 detectado en 0x$addr"
  fi
done

exit $OK
