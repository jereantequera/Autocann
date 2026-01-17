# Guía de Instalación - Autocann

## Instalación en Raspberry Pi (Producción)

### Paso 1: Instalar uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Importante:** Agregá uv al PATH permanentemente:

```bash
# Agregar al archivo de configuración
echo 'export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# Verificar que funciona
uv --version

# Habilitar I2C
sudo raspi-config # Interfacing Options → I2C -> Enable
sudo apt-get update
sudo apt-get install -y i2c-tools
sudo reboot
```

Si ya tenés uv instalado pero el script no lo encuentra, agregá manualmente el export a tu `~/.bashrc`.

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
source $HOME/.local/bin/env 
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
uv run python -m autocann.cli.backend

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

### "uv: command not found" en start_services.sh

Si el script no encuentra `uv`, el PATH no está configurado correctamente:

```bash
# Agregar permanentemente a ~/.bashrc
echo 'export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# Verificar
uv --version
which uv

# Si sigue sin funcionar, encontrá dónde está uv:
find ~ -name "uv" -type f 2>/dev/null

# Y agregá esa ruta específica al PATH
```

**Nota:** El script `start_services.sh` ahora incluye el PATH automáticamente, pero necesitás que `~/.bashrc` también lo tenga para usar `uv` manualmente.

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

## Administración Remota SSH

### Configuración Inicial (Solo primera vez)

Para trabajar cómodamente desde tu Mac sin ingresar contraseña cada vez:

```bash
# 1. Crear archivo de configuración (opcional pero recomendado)
cp config.mk.example config.mk
nano config.mk

# Editá la IP de tu Raspberry Pi:
RPI_HOST = 192.168.100.37  # Usar IP fija, no .local

# 2. Configurar SSH key (automático)
make ssh-setup

# Esto hará:
# - Generar una clave SSH si no existe
# - Copiar la clave a la Raspberry Pi
# - Te pedirá la contraseña UNA ÚLTIMA VEZ
```

**¿Por qué SSH key?** Sin esto, tenés que ingresar la contraseña cada vez que ejecutás `make ssh`, `make deploy`, etc. Con la key configurada, todo funciona automáticamente.

### Comandos Disponibles

```bash
# Conectarse
make ssh

# Ver logs remotos
make ssh-logs

# Verificar servicios
make ssh-status

# Reiniciar servicios
make ssh-restart

# Deploy completo (desde tu Mac)
make deploy
```

### Workflow de Desarrollo Típico

```bash
# 1. En tu Mac: Editar código
code autocann/cli/vpd.py

# 2. Commit y deploy
git add .
git commit -m "Update VPD logic"
make deploy  # Push, pull en RPi, y reinicia servicios

# 3. Verificar remotamente
make ssh-status
make ssh-logs
```

### Sin config.mk

También podés pasar variables en línea:

```bash
make ssh RPI_HOST=192.168.1.50 RPI_USER=pi
make deploy RPI_HOST=autocann.local
```

---

## Referencias

- [Documentación de uv](https://docs.astral.sh/uv/)
- [Adafruit BME280](https://learn.adafruit.com/adafruit-bme280-humidity-barometric-pressure-temperature-sensor-breakout)
- [README principal](./README.md)

