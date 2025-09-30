# Guía de Instalación - Autocann

## Instalación en Raspberry Pi (Producción)

### Paso 1: Instalar uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Agregá uv al PATH:

```bash
echo 'export PATH="$HOME/.cargo/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### Paso 2: Clonar el repositorio

```bash
cd /home/autocann
git clone <tu-repositorio> Autocann
cd Autocann
```

### Paso 3: Instalar dependencias

En la Raspberry Pi, **necesitás** instalar las dependencias de hardware:

```bash
# Opción 1: Usar el Makefile (recomendado, auto-detecta)
make install

# Opción 2: Instalar manualmente
uv sync --extra rpi
```

Esto instalará:
- Flask, Redis, pytz (dependencias base)
- **adafruit-circuitpython-bme280** (sensores BME280)
- **adafruit-blinka** (capa de compatibilidad)
- **gpiozero** (control GPIO de alto nivel)
- **RPi.GPIO** (control GPIO de bajo nivel)

### Paso 4: Configurar servicios

```bash
# Iniciar Redis
docker run -d --name redis-stack-server -p 6379:6379 redis/redis-stack-server:latest

# Crear directorio de logs
mkdir -p logs

# Verificar instalación
make check
```

### Paso 5: Probar

```bash
# Opción 1: Usar el script de inicio
./scripts/start_services.sh

# Opción 2: Usar el Makefile
make run-backend  # En una terminal
make run-vpd      # En otra terminal
```

---

## Instalación en macOS/Linux/Windows (Desarrollo)

### Paso 1: Instalar uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Agregá uv al PATH (ver output del comando anterior).

### Paso 2: Clonar el repositorio

```bash
cd ~/Documents
git clone <tu-repositorio> Autocann
cd Autocann
```

### Paso 3: Instalar dependencias base

En desarrollo, **NO necesitás** las dependencias de Raspberry Pi:

```bash
# Solo dependencias base (Flask, Redis, pytz)
uv sync
```

**Nota**: Las librerías de GPIO y sensores BME280 no se pueden compilar en macOS/Windows y no son necesarias para desarrollo del backend/frontend.

### Paso 4: Desarrollo

```bash
# Podés trabajar en el backend sin problemas
uv run scripts/backend.py

# Los scripts de GPIO solo funcionan en Raspberry Pi
# pero podés editarlos sin problemas
```

---

## Estructura de Dependencias

### Dependencias Base (siempre instaladas)

```
flask       - Servidor web
redis       - Cliente Redis
pytz        - Manejo de zonas horarias
```

### Dependencias Raspberry Pi (solo con --extra rpi)

```
adafruit-circuitpython-bme280  - Sensores BME280
adafruit-blinka                - Capa de compatibilidad I2C/SPI
gpiozero                       - Control GPIO de alto nivel
RPi.GPIO                       - Control GPIO de bajo nivel
```

### Dependencias de Desarrollo (opcionales)

```bash
uv sync --extra dev
```

---

## Comandos Útiles

### En Raspberry Pi

```bash
# Instalar/actualizar
make install          # Auto-detecta Raspberry Pi
make install-rpi      # Fuerza instalación RPi
make update           # Actualizar dependencias

# Verificar
make check            # Verifica sistema y sensores

# Ejecutar
make run-vpd          # Control VPD
make run-backend      # Servidor web
make logs             # Ver logs
```

### En Desarrollo (macOS/etc)

```bash
# Instalar/actualizar
make install          # Solo dependencias base
make update           # Actualizar dependencias

# Ejecutar
make run-backend      # Servidor web (funciona)
# Los scripts de GPIO no funcionarán (solo en RPi)
```

---

## Solución de Problemas

### "Failed to build RPi.GPIO" en macOS/Linux

✅ **Esto es normal**. RPi.GPIO solo funciona en Raspberry Pi. Usá:

```bash
uv sync  # Sin --extra rpi
```

### "No module named 'adafruit_bme280'" en Raspberry Pi

❌ Falta instalar las dependencias de hardware. Usá:

```bash
uv sync --extra rpi
```

### "i2cdetect: command not found"

Instalá las herramientas I2C:

```bash
sudo apt-get install i2c-tools
```

### Sensores BME280 no detectados

1. Habilitar I2C:

```bash
sudo raspi-config
# Interface Options → I2C → Enable
sudo reboot
```

2. Verificar conexiones:

```bash
sudo i2cdetect -y 1
# Deberías ver 76 y 77
```

---

## Migración desde venv

Si ya tenés el proyecto con `venv/`:

```bash
# Backup del venv antiguo
mv venv venv.backup.$(date +%Y%m%d)

# Instalar con uv
uv sync --extra rpi  # En Raspberry Pi
# o
uv sync              # En desarrollo

# Verificar
make check
```

---

## Referencias

- [Documentación de uv](https://docs.astral.sh/uv/)
- [Adafruit BME280](https://learn.adafruit.com/adafruit-bme280-humidity-barometric-pressure-temperature-sensor-breakout)
- [README principal](./README.md)

