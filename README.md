# Autocann - Cannabis Cultivation Automation System

Sistema de automatización para cultivo de cannabis con control de VPD (Vapor Pressure Deficit), temperatura y humedad usando Raspberry Pi.

## Características

- Control automático de VPD según etapa (vegetativo temprano/tardío, floración, secado)
- Monitoreo de temperatura y humedad interior y exterior
- Control de humidificador / deshumidificador con protección contra ciclado corto
- Dashboard web con Flask
- Almacenamiento híbrido:
  - Redis para estado en tiempo real
  - SQLite para el histórico

## Hardware Requerido

- Raspberry Pi (3B+ o superior)
- Sensor interior: **ESP32 con sensor de temperatura/humedad** que postea a
  `POST /api/sensor/indoor` (modo por defecto), **o** un DHT22 local en GPIO 4
  (modo `--local`)
- Sensor exterior: **DHT22 en GPIO 13**
- Módulo de relés para humidificador, deshumidificador y ventilación
- Docker para Redis

> ⚠️ Versiones anteriores de este README describían dos sensores **BME280 por
> I2C** (0x76 y 0x77). El código nunca los usó: lee DHT22 por GPIO y/o el ESP32
> por HTTP. Si querés volver a I2C, mirá la sección
> [Recomendación de hardware](#recomendación-de-hardware-para-empezar-de-cero) —
> I2C es bastante más confiable que el DHT22.

## Conexión de Sensores

### Interior — ESP32 (por defecto)

El ESP32 postea cada pocos segundos:

```bash
curl -X POST http://<ip-de-la-raspberry>:5000/api/sensor/indoor \
  -H 'Content-Type: application/json' \
  -d '{"temperature": 24.5, "humidity": 62.0}'
```

El loop de control descarta datos de más de 60 segundos y apaga las salidas.

### Interior — DHT22 local (modo `--local`)

- VCC → 3.3V
- GND → GND
- DATA → GPIO 4 (con resistencia pull-up de 4.7k–10k a 3.3V)

### Exterior — DHT22

- VCC → 3.3V
- GND → GND
- DATA → GPIO 13 (con pull-up de 4.7k–10k a 3.3V)

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
uv run python -m autocann.cli.vpd early_veg  # o late_veg, flowering, dry

# Backend web
uv run python -m autocann.cli.backend
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
- `GET /api/current-data` - Estado actual de sensores y control (Redis)
- `GET /api/sensor-history` - Historial de sensores con filtros (SQLite)
- `GET /api/history/aggregated` - Datos agregados para gráficos (SQLite)
- `GET /api/database-stats` - Estadísticas de la base de datos
- `GET /api/sensor-status` - Estado de conectividad de los sensores
- `GET /api/output-status` - Estado de los relés y overrides activos
- `POST /api/output-control` - Override manual de una salida (ver abajo)
- `POST /api/sensor/indoor` - Entrada de datos del ESP32
- `GET /api/vpd-score`, `GET /api/weekly-report`, `GET /api/anomalies` - Analítica

> `GET /api/historical-data` fue eliminado. Guardaba las mismas series en Redis
> que ya están en SQLite, el dashboard nunca lo usó, y el bug que lo alimentaba
> hacía crecer el blob JSON sin límite (ver [CHANGELOG.md](./CHANGELOG.md)).

### Override Manual de Salidas

Sólo el loop de control toca el GPIO (un pin tiene un único dueño). El backend
publica el pedido en Redis con un TTL y el loop lo aplica:

```bash
# Prender ventilación por 10 minutos
curl -X POST http://<ip>:5000/api/output-control \
  -H 'Content-Type: application/json' \
  -d '{"name": "ventilation", "state": true, "duration_seconds": 600}'

# Devolver la salida al control automático
curl -X POST http://<ip>:5000/api/output-control \
  -H 'Content-Type: application/json' \
  -d '{"name": "ventilation", "state": null}'
```

El override expira solo (15 min por defecto, 2 h como máximo), así que un
comando olvidado no deja un humidificador prendido para siempre. Si los sensores
fallan, el failsafe gana: apaga todo e ignora los overrides.

📖 **Ver [USAGE.md](./USAGE.md) para documentación completa de la API y ejemplos de uso.**

## Configuración de Pines GPIO

Los pines GPIO ahora están centralizados en `autocann/config.py` (y usados por `autocann/hardware/outputs.py`).

**Defaults reales (los del código, en numeración BCM):**

| Salida | Pin BCM | Dispositivo |
|---|---|---|
| `humidity_up` | 7 | Humidificador |
| `humidity_down` | 16 | Deshumidificador |
| `ventilation` | 25 | Extracción |

> ⚠️ Este README decía antes humidificador=25 y ventilación=7, al revés que el
> código. El código siempre usó la tabla de arriba, así que **es a eso que están
> conectados tus relés**; no se cambió para no energizar el dispositivo
> equivocado. Verificalo una vez contra tu cableado.

Los relés se asumen **active-low** (el relé cierra cuando el pin está en LOW).
`gpio_pins_from_env()` rechaza una configuración con dos salidas en el mismo pin.

**Overrides por variables de entorno (sin tocar código):**

- `AUTOCANN_PIN_HUMIDITY_UP`
- `AUTOCANN_PIN_HUMIDITY_DOWN`
- `AUTOCANN_PIN_VENTILATION`

Ejemplo:

```bash
AUTOCANN_PIN_HUMIDITY_DOWN=7 uv run python -m autocann.cli.vpd early_veg
```

## Sensores interiores y detección de deriva

Una instalación con un solo sensor no necesita configurar nada: el ESP32 postea
a `/api/sensor/indoor` sin `sensor_id` y el lazo lee la clave de siempre.

Con **dos sensores** se puede detectar la falla que ni el filtro de mediana ni
el failsafe por dato viejo ven: un sensor degradado que sigue informando un
número plausible, estable y en rango, que el control obedece.

```bash
AUTOCANN_INDOOR_PRIMARY=a    # decide: es el que controla la carpa
AUTOCANN_INDOOR_WITNESS=b    # testigo: no controla nada, solo vigila al primario
```

Cada ESP32 postea con su `sensor_id` y cada lectura va a su propia clave
(`esp32_indoor:a`, `esp32_indoor:b`).

**Qué detecta** (`autocann/control/drift.py`):

- **Divergencia.** Dos sensores en distintas partes de la carpa leen distinto
  por motivos reales — posición, flujo de aire, gradiente — así que un offset
  entre ellos no prueba nada. Un **cambio** en ese offset sí. El monitor aprende
  el offset base mientras los dos están sanos y alarma cuando se mueve.
- **Valor congelado.** Cero variación durante una ventana larga.
- **Pegado al tope de escala.** Humedad clavada en 0 % o 100 % sostenido.

El offset base se persiste en Redis sin TTL, a propósito: un sensor deriva en
semanas, así que uno que se reaprendiera en cada reinicio adoptaría en silencio
lo que diga el sensor ya desviado y nunca dispararía. Después de cambiar un
sensor hay que borrarlo:

```bash
redis-cli DEL indoor_drift_baseline
```

El veredicto sale en `sensor_status` bajo la clave `drift`. **Ninguna de las
tres condiciones apaga nada**: un sensor que deriva no es motivo para
desenergizar la carpa — para eso está el failsafe — es motivo para avisarte cuál
sensor desconfiar.

El firmware del nodo está en `firmware/sensor_node/sensor_node.ino`.

## Relés en un nodo remoto

Por defecto los relés se manejan desde el GPIO de la propia Raspberry. Con
`AUTOCANN_RELAY_MODE=remote` el lazo deja de tocar GPIO y sólo **publica el
estado deseado**; un ESP32 al lado de los relés lo consulta y lo aplica.

```bash
AUTOCANN_RELAY_MODE=remote uv run python -m autocann.cli.vpd
```

### Por qué el nodo consulta en vez de recibir

La Pi podría postearle al nodo, pero entonces tendría que conocer su IP, y con
DHCP eso se rompe solo. Consultando, el nodo usa la misma dirección fija que ya
usa el de sensores.

Y lo más importante: **la consulta es el latido**. Una consulta fallida es un
latido perdido, así que un solo mecanismo cubre WiFi caída, Pi colgada, backend
muerto y cable desenchufado — sin temporizadores separados que puedan
desincronizarse.

### El estado completo, cada ciclo

`Relays.publish_state()` escribe las tres salidas con timestamp en **cada**
iteración, aunque no haya cambiado nada. No es redundancia:

- el silencio es lo que le dice al nodo que el lazo murió;
- si el nodo desenergiza por su cuenta (watchdog), el caché del lazo seguiría
  diciendo "encendido" y `set_output` nunca volvería a mandar el comando.
  Mandando el estado entero cada vez, esa divergencia no puede existir.

### Las barreras

| Falla | Qué la cubre |
|---|---|
| El lazo deja de publicar | `GET /api/outputs/desired` responde **todo apagado** pasados 30 s |
| El nodo no alcanza a la Pi | watchdog local a los 60 s → todo apagado |
| Respuesta incompleta o corrupta | el nodo la descarta entera; aplicar medio estado es peor que no aplicar |
| ESP32 colgado, reseteado o sin alimentación | pines en alta impedancia + cargas en **NO** → relés abiertos |
| Todo lo anterior | humidistato mecánico en serie con el humidificador |

Ninguna depende de que la anterior haya funcionado.

### Cableado

Placa de 4 canales opto, activo-bajo, **con el jumper JD-VCC sacado**:

```
GND  → GND del ESP32  +  negativo de la fuente de 5 V (cable corto y grueso)
IN1  → GPIO 25   humidity_up
IN2  → GPIO 26   humidity_down
IN3  → GPIO 27   ventilation
IN4  → libre
VCC  → 3V3 del ESP32          ⚠️ nunca 5 V: los pines IN reposarían en 5 V

JD-VCC → +5 V de una fuente aparte
VCC (header de 2 pines) → nada, es la misma red que el VCC de señal
```

**Las cargas van en COM + NO, nunca en NC.** Es lo que hace que desenergizado
signifique apagado, y de eso dependen todas las barreras de arriba.

El firmware está en `firmware/relay_node/relay_node.ino`.

## Administración Remota (SSH)

### Configuración SSH (Primera vez)

Para facilitar la administración remota, primero configurá SSH sin contraseña:

```bash
# 1. Crear archivo de configuración (opcional)
cp config.mk.example config.mk
# Editá config.mk con la IP de tu Raspberry Pi

# 2. Configurar SSH key (solo primera vez)
make ssh-setup
# Esto genera una clave SSH y la copia a la Raspberry Pi
# Te pedirá la contraseña una última vez

# 3. ¡Listo! Ahora podés conectarte sin contraseña
make ssh
```

**Sin config.mk:** También podés pasar la IP directamente:

```bash
make ssh-setup RPI_HOST=192.168.100.37
make ssh RPI_HOST=192.168.100.37
```

### Comandos SSH Disponibles

```bash
# Conectarse a la Raspberry Pi
make ssh

# Ver logs remotos
make ssh-logs

# Ver estado de servicios
make ssh-status

# Reiniciar servicios
make ssh-restart

# Deploy completo (push + pull + restart)
make deploy
```

### Variables de Configuración

Podés personalizar la conexión SSH:

```bash
# En línea de comandos
make ssh RPI_HOST=192.168.1.50 RPI_USER=pi

# O crear config.mk:
RPI_USER = tu_usuario
RPI_HOST = 192.168.1.100
RPI_PATH = /ruta/al/proyecto
```

**Valores por defecto:**
- `RPI_USER`: `autocann`
- `RPI_HOST`: `autocann.local`
- `RPI_PATH`: `/home/autocann/Autocann`

## Desarrollo

### Estructura de Dependencias

El proyecto usa grupos de dependencias opcionales:

- **Base** (Flask, Redis, pytz): Siempre instaladas
- **rpi** (gpiozero, RPi.GPIO, adafruit-blinka, adafruit-circuitpython-dht): Solo en Raspberry Pi
- **dev** (Ruff, pytest): Herramientas de desarrollo

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

### Tests y Linting

```bash
make test     # pytest
make lint     # ruff
make status   # qué procesos están corriendo
```

La lógica de control es pura (`autocann/control/`), así que los tests corren sin
hardware ni Redis.

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

#### Redis (Datos en Tiempo Real)

```bash
docker exec redis-stack-server redis-cli SAVE
docker cp redis-stack-server:/data/dump.rdb ./backup/
```

#### SQLite (Datos Históricos)

```bash
# Backup simple
cp data/autocann.db data/autocann_backup_$(date +%Y%m%d).db

# Backup usando SQLite dump
sqlite3 data/autocann.db .dump > backup_$(date +%Y%m%d).sql
```

📖 **Ver [USAGE.md](./USAGE.md) para más información sobre el sistema de almacenamiento.**

## Troubleshooting

### Los relés golpean (clic-clic-clic)

Casi siempre es **más de un loop de control corriendo a la vez**, peleándose los
mismos pines:

```bash
make status          # ¿cuántos procesos de autocann.cli.vpd hay?
```

Si hay más de uno, pará todo y arrancá uno solo:

```bash
pkill -f autocann.cli.vpd && ./scripts/start_services.sh
```

Si hay uno solo y aún así cicla rápido, subí los mínimos en
`autocann/control/humidity.py` → `ControllerConfig` (`min_on_seconds`,
`min_off_seconds`, `min_changeover_seconds`). Para un deshumidificador con
compresor, `min_off_seconds = 300` es más sano.

### El sensor DHT22 falla o da lecturas raras

El DHT22 es ruidoso y sensible al largo del cable y al ruido eléctrico de los
relés. El loop ya filtra con mediana de 5 muestras y apaga todo si no hay dato
fresco, pero si el problema persiste mirá la
[recomendación de hardware](#recomendación-de-hardware-para-empezar-de-cero).

```bash
make check           # chequea dependencias, Redis, base de datos, GPIO y procesos
```

### Sensores I2C no detectados

Si volvés a sensores I2C (BME280, SHT3x, SCD4x):

```bash
sudo raspi-config    # Interface Options → I2C → Enable
sudo i2cdetect -y 1
./scripts/check_i2c_bme.sh 44 45   # las direcciones que esperás
```

### Permisos GPIO

Si tenés problemas de permisos con GPIO:

```bash
sudo usermod -a -G gpio $USER
sudo reboot
```

## Recomendación de Hardware para Empezar de Cero

Si vas a rearmar esto, 📖 **[HARDWARE.md](./HARDWARE.md)** tiene el diagnóstico de
por qué aparecen las interferencias y qué componentes elegir.

Resumen: el DHT22 es la pieza más débil (cambialo por un **SHT41 por I2C**), los
relés necesitan **aislación óptica con fuente de bobina separada y snubbers RC**,
y el cambio más grande en confiabilidad es **mover el lazo de control a un ESP32
al lado de los sensores**, dejando la Raspberry como dashboard. También conviene
pasar de `start_services.sh` a **systemd**, que garantiza una sola instancia.

## Licencia

[Tu licencia aquí]

## Contacto

[Tu información de contacto aquí]

