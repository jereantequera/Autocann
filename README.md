# Autocann - Cannabis Cultivation Automation System

Sistema de automatización para cultivo de cannabis con control de VPD (Vapor Pressure Deficit), temperatura y humedad usando Raspberry Pi.

## Características

- Control automático de VPD basado en etapa de crecimiento (vegetativo temprano, vegetativo tardío, floración, secado)
- Monitoreo de temperatura y humedad interior y exterior con sensores BME280
- Control de humidificadores/deshumidificadores
- Control de ventilación
- Sistema de riego automatizado
- Dashboard web con Flask para visualización de datos
- Almacenamiento histórico de datos en Redis

## Hardware Requerido

- Raspberry Pi (3B+ o superior recomendado)
- 2x Sensores BME280 (I2C)
  - Sensor interior: dirección 0x76 (SD0 → GND)
  - Sensor exterior: dirección 0x77 (SD0 → VCC)
- Relés para control de dispositivos (humidificador, deshumidificador, ventilación, riego)
- Docker para Redis

## Conexión de Sensores BME280

### Sensor Interior
- VCC → 3.3V (pin 1)
- GND → GND (pin 6)
- SCL → GPIO3/SCL (pin 5)
- SDA → GPIO2/SDA (pin 3)
- SD0 → GND (dirección I2C 0x76)

### Sensor Exterior
- VCC → 3.3V (pin 1, compartido)
- GND → GND (pin 9)
- SCL → GPIO3/SCL (pin 5, compartido)
- SDA → GPIO2/SDA (pin 3, compartido)
- SD0 → VCC (dirección I2C 0x77)

## Instalación

### Instalación Rápida

**En Raspberry Pi (producción):**

```bash
# Instalar uv
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.cargo/bin:$PATH"

# Clonar e instalar
cd /home/autocann
git clone <tu-repositorio> Autocann
cd Autocann
make install  # Auto-detecta Raspberry Pi e instala todo
make check    # Verifica que todo funcione
```

**En macOS/Linux/Windows (desarrollo):**

```bash
# Instalar uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clonar e instalar
git clone <tu-repositorio> Autocann
cd Autocann
make install  # Solo instala dependencias base (Flask, Redis, etc)
```

📖 **Ver [INSTALL.md](./INSTALL.md) para instrucciones detalladas y solución de problemas.**

### Configurar Redis

```bash
docker run -d --name redis-stack-server -p 6379:6379 redis/redis-stack-server:latest
```

## Uso

### Iniciar los Servicios

El script `start_services.sh` inicia todos los servicios necesarios:

```bash
./scripts/start_services.sh
```

### Ejecutar Scripts Individuales

Con uv, podés ejecutar los scripts directamente:

```bash
# Control de VPD
uv run scripts/fix-vpd.py early_veg  # o late_veg, flowering, dry

# Backend web
uv run scripts/backend.py

# Sistema de riego
uv run scripts/watering.py
```

## Etapas de Crecimiento

El sistema soporta diferentes etapas con rangos de VPD específicos:

- **early_veg**: VPD 0.6-1.0 kPa (vegetativo temprano)
- **late_veg**: VPD 0.8-1.2 kPa (vegetativo tardío)
- **flowering**: VPD 1.2-1.5 kPa (floración)
- **dry**: Humedad 60-65% (secado)

## Dashboard Web

El backend Flask proporciona un dashboard web accesible en:

```
http://<ip-de-tu-raspberry>:5000
```

### Endpoints API

- `GET /` - Dashboard principal
- `GET /api/current-data` - Datos actuales de sensores
- `GET /api/historical-data` - Datos históricos (6h, 12h, 24h, 1 semana)

## Configuración de Pines GPIO

Los pines GPIO están configurados en `scripts/fix-vpd.py`:

- Pin 25: Control de humidificador (subir humedad)
- Pin 16: Control de deshumidificador (bajar humedad)
- Pin 7: Control de ventilación
- Pin 24: Control de riego (en `watering.py`)

## Desarrollo

### Estructura de Dependencias

El proyecto usa grupos de dependencias opcionales:

- **Base** (Flask, Redis, pytz): Siempre instaladas
- **rpi** (GPIO, BME280): Solo en Raspberry Pi
- **dev** (Ruff): Herramientas de desarrollo

```bash
# Instalar grupo específico
uv sync --extra rpi   # Raspberry Pi
uv sync --extra dev   # Herramientas dev
uv sync --all-extras  # Todo
```

### Agregar Dependencias

```bash
# Dependencia base (disponible en todos los sistemas)
uv add nombre-del-paquete

# Dependencia específica de Raspberry Pi
# Editar pyproject.toml manualmente en [project.optional-dependencies.rpi]

# Dependencia de desarrollo
uv add --dev nombre-del-paquete
```

### Actualizar Dependencias

```bash
make update
# o
uv sync --upgrade
```

## Mantenimiento

### Logs

Los logs se almacenan en el directorio `logs/`:

- `backend_YYYY-MM-DD.log`: Logs del servidor web
- `errors_vpd_YYYY-MM-DD.log`: Errores del sistema de control de VPD

### Backup de Datos

Los datos históricos se almacenan en Redis. Para hacer backup:

```bash
docker exec redis-stack-server redis-cli SAVE
docker cp redis-stack-server:/data/dump.rdb ./backup/
```

## Troubleshooting

### Sensores BME280 no detectados

Verificá que I2C esté habilitado en la Raspberry Pi:

```bash
sudo raspi-config
# Interface Options → I2C → Enable
```

Verificá las direcciones I2C:

```bash
sudo i2cdetect -y 1
```

Deberías ver `76` y `77` en la salida.

### Permisos GPIO

Si tenés problemas de permisos con GPIO:

```bash
sudo usermod -a -G gpio $USER
sudo reboot
```

## Licencia

[Tu licencia aquí]

## Contacto

[Tu información de contacto aquí]

