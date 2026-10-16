# Autocann - Cannabis Cultivation Automation System

Sistema de automatización para cultivo de cannabis con control de VPD (Vapor Pressure Deficit), temperatura y humedad usando Raspberry Pi.

## Características

- Control automático de VPD según etapa (plántula, vegetativo temprano/tardío, floración, secado)
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

El exterior **no alimenta ninguna decisión**: sólo se grafica. Por eso se lee en
un hilo aparte, una vez por minuto, y el lazo de control toma el último valor
cacheado sin bloquearse nunca. Si el sensor deja de contestar, el valor envejece
y a los cinco minutos se apaga: los campos salen en `None` y la tarjeta queda en
`--`. Nunca se copian ahí los valores de interior.

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
cd Autocann/apps/tent
make install  # Auto-detecta Raspberry Pi e instala todo
make check    # Verifica que todo funcione
```

**En macOS/Linux/Windows (desarrollo):**

```bash
# Instalar uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clonar e instalar
git clone <tu-repositorio> Autocann
cd Autocann/apps/tent
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
uv run python -m autocann.cli.vpd seedling  # o early_veg, late_veg, flowering, dry

# Backend web
uv run python -m autocann.cli.backend
```

## Etapas de Crecimiento

El sistema soporta diferentes etapas con rangos de VPD específicos:

- **seedling**: VPD 0.4-0.8 kPa (plántula; se riega con agua sola)
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
- `GET /api/health` - **¿Anda?** El veredicto único: estado de cada sensor, del
  lazo de control y del histórico, con el motivo y qué mirar, en castellano
- `GET /api/sensor-status` - Estado de conectividad de los sensores
- `GET /api/sensors/indoor` - Todos los sensores interiores a la vez, con su rol
  (primario/testigo/observación), la antigüedad de cada lectura y el veredicto
  de deriva
- `POST /api/node/log` - El reporte propio de un nodo ESP32: contadores por
  sensor y las líneas que imprimió
- `GET /api/node/log`, `GET /api/nodes` - Ese log y ese diagnóstico, para el
  dashboard
- `GET`/`POST /api/test-mode` - Pausa: el lazo deja de decidir, hasta que alguien
  la saca
- `POST /api/test-mode/outputs` - Accionar un relé a mano mientras está en pausa
- `GET /api/output-status` - Estado de los relés y overrides activos, con la
  placa y el pin que efectivamente conmutan cada salida
- `POST /api/output-control` - Override manual de una salida (ver abajo)
- `POST /api/sensor/indoor` - Entrada de datos del ESP32
- `GET /api/sensor/indoor` - La última lectura del primario
- `GET /api/outputs/desired` - El estado deseado que consulta el nodo de relés
  (ver [Relés en un nodo remoto](#relés-en-un-nodo-remoto))
- `GET /api/vpd-score`, `GET /api/weekly-report`, `GET /api/anomalies` - Analítica
- `GET /api/period-summary` - Resumen de un período para las tarjetas
- `GET /api/config` - Etapas, rangos y salidas, para que el front no los duplique
- `GET /api/vpd-band` - La banda de humedad de una etapa por temperatura, con la misma cuenta que el lazo
- `GET /api/logbook` - El diario de la carpa: salidas, riegos y cambios de etapa, lo más nuevo primero
- `GET`/`POST /api/calibration` - Offsets por sensor, reversibles
- `GET`/`PUT /api/quiet-hours` - Las franjas de silencio por salida
- `GET`/`POST /api/grows`, `GET /api/grows/active`,
  `POST /api/grows/<id>/activate`, `POST /api/grows/<id>/end`,
  `GET /api/grows/<id>/timeline`, `PUT /api/grows/<id>/stage` - Cultivos y etapas
- `GET`/`PUT /api/feed-plan`, `GET /api/feeding/today` - La grilla de
  fertilización y la dosis que toca hoy
- `GET`/`POST /api/irrigations`, `DELETE /api/irrigations/<id>` - El registro de riegos
- `GET /api/auth`, `POST /api/login`, `POST /api/logout` - El PIN, cuando está
  configurado (ver [Un PIN delante del panel](#un-pin-delante-del-panel))

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

> [!IMPORTANT]
> Esta tabla vale **sólo con `AUTOCANN_RELAY_MODE=gpio`**. Con los relés en un
> nodo remoto los pines que están en el circuito son los del nodo, no éstos —
> ver [Relés en un nodo remoto](#relés-en-un-nodo-remoto).

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

Con **dos sensores en el mismo aire** se puede detectar la falla que ni el
filtro de mediana ni el failsafe por dato viejo ven: un sensor degradado que
sigue informando un número plausible, estable y en rango, que el control
obedece.

Un sensor interior puede tener uno de **tres roles**:

```bash
AUTOCANN_INDOOR_PRIMARY=a      # decide: es el que controla la carpa
AUTOCANN_INDOOR_WITNESS=b      # testigo: no controla, vigila al primario por deriva
AUTOCANN_INDOOR_OBSERVERS=c,d  # observación: se leen y se muestran, nada más
```

El **testigo** tiene que estar en el mismo aire que el primario, porque de eso
depende que el offset entre los dos sea estable. Un **observador** no controla
ni se cruza con nadie: existe porque un sensor puede valer la pena de leer
siendo inútil como testigo. Darle un rol en vez de borrarlo es lo que lo
mantiene en el panel.

> [!IMPORTANT]
> **Esta carpa hoy NO tiene detección de deriva.** Producción corre con
> `AUTOCANN_INDOOR_WITNESS` vacío y `AUTOCANN_INDOOR_OBSERVERS=b`, así que
> `/api/health` informa `has_witness: false` y `drift: null`.
>
> El motivo es físico y deliberado: `b` está **encima de la lámpara**, porque
> interesa saber la temperatura ahí arriba, y no se va a mover. Eso lo
> descalifica como testigo — el offset contra `a` se abre y se cierra con el
> fotoperíodo, así que el detector mediría la luz y no la salud del sensor. Con
> una sola mediana no se describen dos regímenes: el base queda en el medio,
> alarma en cada transición y tapa la deriva real el resto del tiempo. Medido el
> 2026-10-12: base +2,40 °C, diferencia instantánea +0,9 °C, o sea 1,5 °C de
> separación, que es justo el umbral.
>
> Lo que devuelve la detección es un **tercer DHT22 al lado de `a`**, a la
> sombra y al nivel del canopy. Hasta entonces la degradación silenciosa por UV
> —la falla que se llevó el juego de sensores anterior— no tiene vigilancia.

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

A mano, y no hay otra forma: `drift.reset_baseline()` existe en el código pero
**no está cableada a ningún endpoint, CLI ni botón**. Si algún día el cambio de
sensor se vuelve rutina, ahí está la función esperando.

El veredicto sale en `sensor_status` bajo la clave `drift`. **Ninguna de las
tres condiciones apaga nada**: un sensor que deriva no es motivo para
desenergizar la carpa — para eso está el failsafe — es motivo para avisarte cuál
sensor desconfiar.

**En el dashboard**, la sección *Sensores Interiores* muestra una tarjeta por
sensor con su lectura cruda — lo que posteó el nodo, antes del filtro de mediana
y de la calibración — y, **cuando hay testigo configurado**, una tarjeta de
diferencia (testigo − primario) contra el offset base aprendido. Con un
observador en vez de un testigo esa tarjeta no aparece, porque no habría nada
contra qué comparar. Las tarjetas grandes de arriba siguen mostrando el
primario ya filtrado, que es el número sobre el que decide el control, así que
pueden diferir unas décimas y ambas estar bien.

La lista sale de `GET /api/sensors/indoor`, que lee `AUTOCANN_INDOOR_PRIMARY`,
`AUTOCANN_INDOOR_WITNESS` y `AUTOCANN_INDOOR_OBSERVERS`: **el backend también
necesita esas variables**, no sólo el lazo. Sin ellas responde como instalación
de un solo sensor y los demás sensores no aparecen en pantalla.
`scripts/start_services.sh` las exporta antes de arrancar los dos procesos.

El firmware del nodo está en `firmware/sensor_node/sensor_node.ino`.

### ¿Anda el sensor?

Tres fuentes contestaban esa pregunta y podían contradecirse: el badge del hero
salía de `sensor_status` (la opinión del lazo sobre el primario), cada tarjeta de
la antigüedad de su propia lectura en Redis, y el cartel de anomalías de la
antigüedad de la última fila en SQLite. Con el sistema en pausa las tres decían
cosas distintas a la vez — un *"sensor posiblemente desconectado"* en rojo arriba
de dos tarjetas verdes, con los sensores posteando perfecto.

Ahora todo eso sale de `GET /api/health`, que mira las tres fuentes juntas y
contesta una sola vez. Cada sensor cae en uno de cuatro estados, y cada uno manda
a un lugar distinto:

| Estado | Qué pasó | Qué mirar |
|---|---|---|
| **Posteando** | Lectura fresca | — |
| **Postea, pero sospechoso** | Contesta en rango y el detector de deriva lo marcó | Un termómetro de referencia |
| **Dejó de postear** | La última lectura venció | El log del nodo: WiFi, backend o DHT22 |
| **Nunca posteó** | No hay ninguna lectura con ese id | El id en el firmware y el cableado |

El panel muestra además dos renglones que **no** son el estado de un sensor y se
confunden con él todo el tiempo: si el lazo está controlando, en pausa o caído, y
si el histórico se está guardando. La carpa puede quedarse sin control con los
sensores perfectos, y el histórico puede dejar de escribirse con todo lo demás
andando.

El detector de anomalías sigue leyendo la base — que es el lugar correcto para
buscar un hueco en el histórico y el equivocado para buscar un sensor muerto: los
dos son la misma cantidad de filas. Lo que encuentra se relee contra lo que está
pasando ahora antes de salir: con el sistema en pausa el hueco es esperado y no
se informa, y con el sensor que controla caído, el lazo caído o el histórico
frenado lo dice sólo el cartel rojo de arriba, no también la caja de anomalías.
Cada hecho tiene un solo cartel; la tabla está en `USAGE.md`.

### El log del nodo, desde el navegador

El ESP32 imprime por el puerto serie exactamente lo que está mal — un DHT22 que
no contesta, un POST que volvió 400, el WiFi que se cayó — y eso era invisible:
leerlo significa entrar a la carpa con una notebook y **desenchufar el nodo de su
fuente para enchufarlo al USB**, lo que lo reinicia y borra justo el estado que
se quería mirar.

El nodo postea ahora su propio log a `POST /api/node/log` cada 30 s, y se lee en
**Sistema → Nodos ESP32**, junto con su firmware, su IP, la señal WiFi, el motivo
del último arranque y los contadores de lecturas y de POST por sensor. Lo que
manda son **eventos** y un latido cada cinco minutos, no cada lectura: encolar la
lectura de cada ciclo llenaría el log de números normales y tiraría afuera la
línea que explica la falla. La Raspberry guarda las últimas 300 líneas en Redis.

Los contadores por sensor son lo que distingue un DHT22 con un cable flojo de uno
sano: una falla suelta cada tanto es normal en estos sensores, una de cada tres
es un problema físico.

> El `secrets.h` no cambia: la URL del log se deriva de `API_URL`, así que un
> nodo ya grabado sólo necesita el sketch nuevo. Subí `FW_VERSION` cuando lo
> toques — es lo único que permite saber, mirando la web, si el nodo tiene
> grabado lo último.

## Modos: activo, monitoreo y pausa

El lazo tiene tres estados, y la diferencia entre los dos que no controlan está
en si se guarda historia o no:

| Modo | ¿Decide? | ¿Guarda histórico? | Para qué |
|---|---|---|---|
| `active` | sí | sí | lo normal |
| `monitor` | no | **sí** | puesta a punto: ver qué hace la pieza sola |
| `paused` | no | no | meter mano en la instalación |

Los tres se validan contra `CONTROL_MODES` cuando llegan por `/api/test-mode`, y
los dos últimos son `UNCONTROLLED_MODES`: el lazo publica el estado deseado
igual, pero no elige nada.

### Monitoreo — mirar sin manejar

**El lazo no decide y la historia se escribe igual.** Antes había dos estados
nada más, y "no decidas" y "no guardes" eran el mismo camino de código, porque
la rama de pausa corta antes de llegar al acumulador.

Para trabajo de banco eso está bien: si alguien le sopla a un sensor y fuerza
relés a mano, grabarlo mete basura en la historia que después se lee como
comportamiento real de la carpa. Pero es exactamente lo contrario de lo que hace
falta en la **puesta a punto**, que es la otra razón por la que una carpa está
sin controlar. Antes de que germinen las semillas, lo que la pieza hace sola es
el dato más útil que hay: cuánto se va de noche con nada prendido, si los dos
sensores coinciden, en qué humedad se queda. Ese es justamente el baseline que
uno quiere **antes** de que haya plantas, y la carpa lo estaba tirando.

Así que en monitoreo: nada decide, nada conmuta, todo se registra.

### Pausa — para meter mano

**El lazo deja de decidir y no se guarda nada.** Los
relés quedan donde se los ponga desde el dashboard, y ni las muestras ni los
eventos de control llegan a SQLite — una sesión moviendo relés a mano no es
historia del cultivo.

Se activa desde el chip de estado del encabezado, que dice *Sistema activo* o
*Sistema en pausa* y es también el botón que lo cambia. Mientras está en pausa
hay una barra permanente abajo del encabezado, en todas las vistas: la única
falla que esta función puede producir es que alguien se vaya sin darse cuenta
de que la carpa quedó sin control.

**Dura hasta que alguien la saca.** Antes vencía sola a las 8 h, con el
argumento de que un flag olvidado deja la carpa sin control por días. El riesgo
era real pero el remedio apuntaba al lado equivocado: una pausa que termina por
reloj cierra los relés en un momento que nadie eligió, y el motivo por el que
uno pausa es justamente meter mano en la carpa. Lo que esa preocupación pide de
verdad es que el estado sea imposible de pasar por alto, y eso se dice en
pantalla en vez de hacérselo a los relés.

**La verdad está en la base, Redis es un espejo.** El lazo lee Redis en cada
ciclo porque es el camino rápido, y repone el espejo desde la base cada 30 s,
antes de evaluar la pausa, para que un Redis vacío no alcance a meter un ciclo
de control automático. Reiniciar Redis o la Raspberry ya no reanuda la carpa:
era la misma sorpresa que las 8 horas y llegaba con menos aviso todavía. Un
Redis **inalcanzable** sí vuelve a automático, que es distinto — sin Redis
tampoco se pueden mover los relés a mano, así que quedarse en pausa sin que
nadie pueda intervenir no le sirve a nadie.

El registro guarda `{"active", "since", "mode"}`. Uno escrito antes de que
existiera el monitoreo no tiene `mode`, y **se lee como pausa**: es lo que
significaban todos, y adivinar al revés habría devuelto al controlador una
carpa que alguien detuvo a propósito, justo en el deploy que agregó el tercer
estado.

Lo que **sigue valiendo** durante la pausa:

- Los **mínimos de encendido/apagado**. Protegen el equipo, y al equipo no le
  importa si la orden vino de una persona. Un comando que tiene que esperar se
  informa en la tarjeta, no se descarta en silencio.
- **Publicar el estado deseado cada ciclo**. El nodo remoto desenergiza a los
  60 s de silencio, así que un lazo callado haría caer los relés cada minuto.

> [!NOTE]
> Con la pausa puesta no se guarda histórico, y el hueco crece mientras dure.
> `/api/anomalies` lo sabe y no lo reporta como sensor desconectado.

## Franjas de silencio

Una ventana por salida en la que el controlador **no puede elegir arrancarla**.
Lo que el código llama "deshumidificador" en esta instalación es un ventilador
de extracción exterior, y es ruidoso: hacerlo andar a las cuatro de la mañana es
un costo sin contrapartida.

Se configuran en *Sistema → Franjas de silencio*, o por `GET`/`PUT
/api/quiet-hours`. Cada ventana tiene `enabled`, `start`, `end` y un
`max_humidity` opcional. Las ventanas que cruzan la medianoche funcionan
(00:00–09:00 es la que está puesta hoy).

**Restringe la decisión, no el relé.** Forzar un contacto abierto por detrás del
controlador dejaría su contabilidad de tiempos mínimos describiendo un equipo
que no está andando: creería que el ventilador estuvo toda la noche en marcha, y
el primer comando después de la ventana podría cortarle el ciclo al de verdad.
Acá el controlador simplemente nunca elige esa acción, así que todos sus timers
siguen siendo ciertos.

Tres cosas quedan **afuera** del alcance de la ventana, a propósito:

- **Un comando manual.** Alguien que prende un ventilador a las 4 AM decidió
  algo que esto no puede saber.
- **Apagar.** La ventana bloquea arrancar, nunca detener, y nunca le corta un
  tiempo mínimo de encendido. Un equipo que ya venía andando cuando se abre la
  ventana termina el mínimo que lo protege y después se queda apagado.
- **El escape por humedad.** `max_humidity` es la humedad a partir de la cual la
  ventana se ignora, porque el silencio no vale un cultivo: una noche al 85 % es
  hongo. Dejalo sin definir para un bloqueo duro.

Una ventana con la configuración corrupta **no aplica**: se parsea sin tirar
excepción, porque una fila ilegible no puede impedirle al lazo seguir
controlando.

## Un PIN delante del panel

La API conmuta hardware. Cualquiera en la LAN podría cerrar un relé, pausar el
controlador o borrar la grilla de fertilización. El PIN es la respuesta mínima a
eso, y es **opcional**:

```bash
AUTOCANN_PIN=1234    # en el entorno de los servicios
```

**Sin la variable, el comportamiento es exactamente el de antes**: nada pide
nada. Un deploy que se olvida de la variable no puede dejar al cultivador
afuera de su propio panel — no poder ver la carpa es peor que que la vea
alguien más. Hoy producción corre **sin PIN configurado**.

Con PIN: la sesión dura 30 días, y una dirección que falla 10 veces espera 5
minutos antes de poder seguir probando.

**Tres endpoints están exentos a propósito y para siempre:**

| Endpoint | Por qué |
|---|---|
| `GET /api/outputs/desired` | un 401 acá **desenergiza la carpa** a los 60 s, cuando al nodo se le vence el watchdog |
| `POST /api/sensor/indoor` | dos números dentro de rangos físicos y un id contra un patrón estricto |
| `POST /api/node/log` | líneas de log a un buffer circular acotado |

El primero es de sólo lectura y los otros dos están validados antes de tocar
nada. Lo que vale la pena proteger es la mitad de la API que mueve relés, y esa
mitad queda cubierta. Hay tests que fallan si alguien les saca la exención.

> [!WARNING]
> No hay HTTPS, así que el PIN viaja en claro por la LAN. Esto es una barrera
> contra el accidente y el curioso de la misma red, no contra un atacante.

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

### Qué chequea `make check` en este modo

Mira la placa que conmuta, no la que está al lado. En remoto nombra el nodo y
sus GPIO, y verifica lo único que esta punta puede romper: que el lazo esté
publicando `desired_outputs` y que lo publicado entre en la ventana de 30 s de
la primera barrera. Si no, falla diciendo que el nodo va a recibir todo apagado
y manda al lazo, que es quien publica.

**Del nodo no afirma nada**, porque no se puede: sólo consulta y no reporta
nada, así que en la Pi no queda registro de que haya consultado. Lo que informa
es qué se le serviría a cualquier nodo que consulte ahora.

En `AUTOCANN_RELAY_MODE=gpio` sigue abriendo un pin del header, que ahí sí es
suyo. Antes lo hacía en los dos modos: con los relés en el nodo eso daba verde
sobre una placa que no está en el circuito, incluso con el lazo muerto y los
tres relés caídos.

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

### El mapeo del nodo también vive en el backend

El dashboard muestra el pin de cada salida, así que tiene que saber cuál es. El
nodo no lo informa — sólo consulta —, y enseñarle a informarlo es reflashearlo
por USB adentro de la carpa. Así que el mapeo está en `RelayNodePins`
(`autocann/config.py`) **copiado del firmware**, que sigue siendo la autoridad:
`tests/test_relays.py` lee el `.ino` y falla si los dos se separan.

| Variable | Default | Qué es |
|---|---|---|
| `AUTOCANN_RELAY_NODE_PIN_HUMIDITY_UP` | 25 | GPIO del nodo, IN1 |
| `AUTOCANN_RELAY_NODE_PIN_HUMIDITY_DOWN` | 26 | GPIO del nodo, IN2 |
| `AUTOCANN_RELAY_NODE_PIN_VENTILATION` | 27 | GPIO del nodo, IN3 |
| `AUTOCANN_RELAY_NODE_NAME` | `ESP32 relés` | Con qué nombre aparece la placa en el panel |

Si reflasheás el nodo con otros pines, cambiá el firmware y después corré
`make test`: el test te va a decir qué quedó desalineado.

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
- `RPI_PATH`: `/home/autocann/Autocann/apps/tent`

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
make check           # dependencias, Redis, base, relés y procesos
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

