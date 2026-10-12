# Changelog

## Modo prueba

Trabajo de banco con la carpa vacía: **el lazo deja de decidir y no se guarda
nada**. Los relés quedan donde se los ponga desde el dashboard; ni las muestras
ni los eventos de control llegan a SQLite, porque una sesión moviendo relés a
mano no es historia del cultivo.

**Se guarda como un vencimiento, no como un flag**, en Redis y con la TTL
puesta: la clave venciendo *es* el modo terminando, y el timestamp adentro es un
segundo chequeo por si la TTL se pierde. Un flag que alguien se olvida sobrevive
reinicios y deja la carpa sin control por días sin que nada avise. Por lo mismo
va en Redis y no en la base: perderlo vuelve a automático, que es la dirección
segura para fallar.

Lo que **no** se suspende, y por qué:

- **Los mínimos de encendido/apagado.** Protegen el equipo, y al equipo no le
  importa si la orden vino de una persona o del controlador. Un comando que
  tiene que esperar se **informa** en la tarjeta — "esperando 33 s" — en vez de
  descartarse en silencio, que es lo que hace que un botón parezca roto.
- **Publicar el estado deseado en cada ciclo.** El nodo remoto desenergiza a los
  60 s sin poll, así que un lazo que se callara en modo prueba haría caer los
  relés una vez por minuto.

Lo pedido manual existente **no se reutilizó**: esos overrides vencen solos a
las 2 h, lo que pelea con el uso de banco, y sobre todo seguirían ahí cuando el
modo prueba termine, pisando decisiones reales. El modo prueba escribe en su
propia clave, que se ignora apenas deja de estar activo.

**Un relé que no se movía no decía por qué.** Dos arreglos que sólo se ven en
esta pantalla:

- Una salida que nunca cambió de estado no tenía clave en Redis — `set_output`
  corta cuando no hay cambio — y el dashboard leía "ausente" como "desconocido",
  dibujando un guión sobre un relé que estaba apagado con certeza.
- Al **matar el lazo estando en modo prueba**, el `all_off` de apagado escribía
  un evento: los toggles de banco terminaban en el histórico, uno por sesión.
  El silencio del log pasó a ser un estado del objeto `Relays` y no un
  argumento en cada llamada, porque los caminos que desenergizan sin que nadie
  se los pida — el handler de apagado, el catch-all del lazo — son justo los
  que se olvidarían de pasarlo.

## El sensor de arriba, visible y sin cruzar

El segundo DHT22 está **encima de la lámpara a propósito**: interesa saber la
temperatura ahí. Eso lo descalifica como testigo. El detector de deriva asume
que el offset entre dos sensores es una constante de posición, así que un cambio
delata a uno de los dos; con uno arriba y uno al nivel del canopy el offset se
abre y se cierra con el fotoperíodo, y lo que mide es la luz. Medido acá: base
aprendida +2.40 °C contra una diferencia instantánea de +0.9 °C — 1.5 °C de
separación, que es exactamente el umbral de divergencia. Estaba por empezar a
alarmar.

Un sensor puede tener ahora un tercer rol, **`observer`**: se lee y se muestra,
no controla y no se cruza. Sacarlo de la configuración lo habría sacado también
del dashboard, que es lo contrario de para qué está.

El dashboard dejó de deducir el cross-check contando tarjetas y pregunta si hay
testigo, porque varios sensores no son un cross-check. Sin testigo el badge lo
dice en voz alta en vez de quedarse en blanco: la deriva silenciosa no la está
vigilando nadie, y un badge vacío se lee como "todo bien". Recuperarlo necesita
un tercer DHT22 al lado del primario, en el mismo aire.

---

## Los dos sensores interiores, visibles

La carpa tiene dos DHT22 desde el armado del 11-10: `a` en GPIO 32 controla y
`b` en GPIO 33 lo vigila. Los dos posteaban, los dos quedaban en Redis y el
monitor de deriva comparaba los dos — pero el dashboard mostraba uno solo, así
que el testigo no servía de nada: no se puede desconfiar de un sensor que no se
ve.

**`GET /api/sensors/indoor`** devuelve los dos a la vez, cada uno con su rol, la
antigüedad de su lectura y sus flags de deriva. El dashboard no podía armar esto
pidiendo `/api/sensor/indoor?sensor_id=` dos veces: qué ids existen y cuál
controla es configuración del servidor, no algo que el front pueda adivinar. Una
instalación de un solo sensor responde con una entrada, sin que el front tenga
que tratarla aparte.

**Sección *Sensores Interiores*** en el dashboard: una tarjeta por sensor con la
lectura cruda — lo que posteó el nodo, antes del filtro de mediana y de la
calibración — y una tarjeta de diferencia (testigo − primario) contra el offset
base aprendido. Mostrar el offset base al lado de la diferencia es lo que separa
"los dos coinciden" de "todavía no aprendió una base": sin eso, un monitor que
nunca juntó suficientes muestras se ve idéntico a uno conforme.

**La diferencia se calcula sólo entre dos lecturas frescas.** Restarle a una
lectura viva una de hace una hora mide el reloj, no los sensores, y en una
pantalla eso se lee como una falla del par.

**El backend no veía los ids de los sensores.** `start_services.sh` exportaba
`AUTOCANN_INDOOR_PRIMARY` / `AUTOCANN_INDOOR_WITNESS` *después* de arrancar el
backend, así que el proceso web quedaba con la configuración de un solo sensor.
Para el lazo alcanzaba; para esta pantalla no.

**Un badge huérfano menos.** El segundo sensor se pintaba desde `data.indoor` —
la visión que el lazo tiene del *primario* — sobre un elemento que no existía en
ningún template. Ahora cada sensor tiene su badge, alimentado por su propia
lectura.

Los nombres de rol (`primary` / `witness`) y el máximo de 60 s de antigüedad
viven ahora en `autocann/config.py`: un dashboard que llame fresca una lectura
que el lazo está descartando es peor que cualquiera de las dos respuestas sola.

Pendiente, a propósito: **los gráficos históricos siguen siendo del primario**.
SQLite guarda las muestras del lazo, y el testigo no pasa por ahí — mostrarlo en
el histórico es un cambio de esquema, no de dashboard.

---

## Fase 2 del roadmap — data más confiable

El histórico tenía huecos de información que ninguna query podía recuperar
después. Cuatro de los cinco puntos de la fase; el cross-check entre sensores
espera a que haya un segundo sensor interior.

**Se guardaba una lectura instantánea, no el intervalo.** El loop lee cada 3
segundos y persiste cada 5 minutos: de ~100 lecturas se guardaba 1 y se tiraban
99. Un pico de 30 segundos podía quedar como si fuera el valor del período, o
desaparecer. Ahora cada fila lleva avg/min/max y `sample_n`. Verificado
end-to-end: una corrida real guardó filas de 39 lecturas con 4 °C de rango.

**No se guardaba el contexto.** `stage`, `indoor_source`, `control_action` y
`quality` por muestra, así que el histórico puede responder qué sensor produjo
una lectura y si el control estaba actuando o en failsafe.

**Calibración por sensor.** Tabla `sensor_calibration` y `GET`/`POST
/api/calibration`. Se guardan los valores crudos además de los corregidos: la
corrección de humedad se satura en 0 y 100, así que invertirla no siempre
recupera lo que dijo el sensor.

**Los huecos se dibujaban como línea recta.** Seis horas caído se unían con una
recta, que se lee como "la temperatura bajó suavemente". La API ahora inserta un
marcador cuando el salto supera 2× el intervalo esperado.

**Banda min–max** sobre el gráfico de temperatura, que es el motivo visual de
guardar min/max.

**Un bug encontrado al implementarlo:** `last_db_save` arrancaba en el pasado,
así que cada arranque escribía una fila que decía representar cinco minutos a
partir de una sola lectura.

Tests: de 369 a **409 de Python**, más los 10 de JavaScript.

---

## Fases 0 y 1 del roadmap — 2026-10-02

Ver [ROADMAP.md](./ROADMAP.md) para el plan completo y el detalle de cada fase.

### Fase 0 — Refactor del frontend

`index.html` pasó de **3013 líneas a 33**: el CSS salió a `static/css/app.css`,
el JS a **9 módulos ES** en `static/js/`, y el HTML a 10 parciales de Jinja. Los
13 `onclick=` se reemplazaron por `data-action` + delegación. Chart.js y el
plugin de anotaciones quedaron vendorizados: el dashboard ya no depende de un
CDN, que en una carpa sin internet fallaba en silencio.

`GET /api/config` sirve los rangos y nombres de etapa, eliminando la copia que
el template tenía de `vpd_math.py`.

Se verificó comparando una huella del dashboard antes y después: 348 → 348
elementos y cero diferencias en los valores en pantalla.

### Fase 1 — Contador de días por etapa

Tabla `stage_events` con migración y backfill idempotente — antes
`grows.stage` era un único valor que se sobreescribía, así que **el dato para
contar días no existía**. La barra de cultivo ahora muestra `día 14 de ~56`,
`cultivo: día 63`, barra de progreso y fecha estimada de fin, y los tres
gráficos marcan los cambios de etapa.

La lógica vive en `autocann/control/stages.py` como funciones puras: se cuenta
en **días de calendario en zona local**, no en bloques de 24 h, así que un
cambio de etapa a las 23:59 marca día 2 un minuto después, que es lo que dice el
calendario.

### Bugs del frontend corregidos

**XSS en la lista de cultivos.** `grow.name` se inyectaba crudo en `innerHTML`.
Como la API no tiene autenticación, cualquiera en la LAN podía crear un cultivo
con un payload que después corría en el navegador del operador, con acceso a
`POST /api/output-control`. Verificado con un nombre malicioso: ahora se
renderiza como texto.

**Un `0.0` real se mostraba como `--`.** Temperatura de hoja de 0 °C y humedad
objetivo de 0 son lecturas válidas.

**"Sistema Activo" era un `<div>` estático** que decía lo mismo con el backend
caído. Ahora el punto se pone rojo y aparece un banner.

**El polling de 3 s seguía corriendo con la pestaña oculta**, y los requests se
apilaban si la Raspberry respondía lento. Ahora hay un request en vuelo por
endpoint, con timeout de 8 s, y el polling se pausa cuando la pestaña no está
visible.

**`new Date("2026-01-16 12:00:00")`** no es un formato que el constructor tenga
que aceptar; Safari devuelve `Invalid Date`. **`localStorage`** lanza excepción
en modo privado. Un día sin muestras se pintaba como un día malo en lugar de
"sin datos".

**Recursión infinita en Chart.js.** `chart.options` es un proxy resolver en v4;
el patrón `plugins.annotation = plugins.annotation || {}` le devuelve un valor
que él mismo resolvió y su setter recursa hasta reventar el stack. Encontrado al
implementar las marcas de etapa.

**`make status` reportaba procesos fantasma en macOS** (`pgrep -af` es sólo de
Linux), y `pkill -f 'autocann.cli.vpd'` mataba al shell que lo ejecutaba — por
ssh eso cortaba el restart a la mitad. Resuelto con el truco de los corchetes
(`[a]utocann`).

### Tests

De 327 a **369 tests de Python** más **10 de JavaScript** (`node --test`, que se
saltean si no hay node). Los nuevos cubren el conteo de días, el backfill, el
timeline, y guards de frontend que impiden que vuelvan los `onclick` inline, el
CDN, los rangos hardcodeados y la auto-asignación sobre el proxy de Chart.js.

---

## Revisión de código — 2026-10-02

### Bugs corregidos

**`query_db stats` tiraba `KeyError`.** `get_database_stats()` devolvía sólo
`grow_count` y `database_size_mb`, pero el CLI imprime siete campos y USAGE.md
documenta los siete. Se restauraron `database_path`, `sensor_data_count`,
`control_events_count`, `oldest_record` y `newest_record`.

**Las series históricas de Redis crecían sin límite.** En
`store_historical_data()` la condición de volcado era
`len(buffer_list) == 1 or <pasó el intervalo>`. Como el buffer se vaciaba después
de cada volcado, la siguiente muestra volvía a tener largo 1 y volcaba de nuevo:
el promediado nunca ocurría. Cada lectura, cada 3 segundos, se agregaba a las
cuatro ventanas. La serie de 1 semana llegaba a ~200.000 puntos (~20 MB de JSON)
que se leían, parseaban y reescribían **cada 3 segundos** en la Raspberry.

El dashboard nunca usó ese endpoint: los gráficos leen de SQLite. Se eliminaron
la función y `GET /api/historical-data`.

**`make deploy` y `make ssh-restart` dejaban dos loops de control vivos.** Los
patrones decían `python.*fix-vpd`, un script que ya no existe, así que ningún
`pkill` mataba nada y `start_services.sh` arrancaba otro proceso al lado. Dos
loops sobre los mismos pines GPIO es la causa clásica de que los relés golpeen.
Lo mismo en `start_services.sh`, cuyo guard buscaba `python backend.py`: cada
corrida levantaba otro backend. Ahora los patrones apuntan a los módulos reales,
hay guard para los dos servicios y el stop manda SIGTERM antes de SIGKILL para
que el loop apague los relés al salir.

**El control manual de salidas no podía funcionar.** `POST /api/output-control`
abría un `gpiozero.OutputDevice`, lo conmutaba y llamaba a `close()`, que libera
el pin y lo devuelve a su estado por defecto: el relé se caía en cuanto
respondía el request. Y si el loop ya tenía el pin, `gpiozero` levantaba
`GPIOPinInUse`. Ahora el backend publica el pedido en Redis con TTL y el loop —
único dueño del GPIO — lo aplica. El override expira solo.

**`store_control_event()` se llamaba en cada iteración.** Mientras una salida
estaba activa se insertaba una fila cada 3 segundos (~29.000 filas por día).
Ahora se registran sólo las transiciones.

**Un 0.0 real se convertía en `None`.** 28 expresiones de la forma
`round(x, 1) if x else None` en las consultas de resumen y analítica trataban un
valor cero como ausente. Lo mismo en la detección de anomalías, donde
`if row["temperature"] and ...` descartaba 0 °C.

**El objetivo de humedad y el chequeo de rango no coincidían.** El objetivo se
calculaba con la temperatura del aire y el chequeo de rango con la temperatura de
hoja, así que el setpoint real quedaba en el borde húmedo de la banda en vez del
centro. Ahora los dos usan temperatura de hoja.

**El score de VPD medía algo distinto a lo que controla el loop.** El loop decide
con `leaf_vpd`; el score contaba `vpd` (del aire). El dashboard podía marcar
"fuera de rango" mientras el control, correctamente, consideraba que estaba en
rango. Las consultas usan `COALESCE(leaf_vpd, vpd)`.

**`"database is locked"` con dos escritores.** Ninguna conexión activaba WAL ni
`busy_timeout`. Ahora todas pasan por `_open()`, que las configura.

**`GET /api/sensor-history` con un solo límite.** Pasar `start` sin `end` (o al
revés) metía un `None` en la comparación SQL. Ahora se completa el límite que
falta y se rechaza un rango invertido.

**El failsafe podía no dispararse nunca.** (Bug introducido y corregido durante
esta revisión.) El filtro de mediana sigue devolviendo su último valor cuando una
lectura falla, así que el loop habría actuado sobre datos viejos
indefinidamente. Se registra el momento de la última lectura cruda exitosa y esa
edad alimenta el failsafe.

**Frontend.** `x !== null` es verdadero para `undefined`, así que un campo
faltante se renderizaba como `undefined°C`. `new Date("2026-01-16 12:00:00")` no
es un formato que el constructor tenga que aceptar y Safari devuelve
`Invalid Date`. `localStorage` lanza excepción en modo privado. Un día sin
muestras se pintaba como un día malo en lugar de "sin datos".

### Mejoras

**Protección contra ciclado corto.** `autocann/control/humidity.py` es un
controlador puro con banda muerta, tiempo mínimo de encendido, tiempo mínimo de
apagado por dispositivo y demora de cambio de sentido. Antes el loop decidía
sobre la lectura cruda cada 3 segundos sin ningún piso: un sensor ruidoso podía
conmutar un relé 28.800 veces por día. En una simulación de 24 h con ruido de
±1.8% RH, las conmutaciones bajan un 39% y el ciclo medio del relé pasa de 4.5 a
7.4 minutos, manteniendo el mismo tiempo en rango.

> **Corrección con datos reales (2026-10-02).** Ese −39% salió de una simulación
> cuyo modelo de carpa no coincide con la real: el histórico de producción tiene
> 3.341 ciclos del humidificador en 123 días, muy por debajo de lo que la
> simulación suponía. El número que **sí** se sostiene es el piso: 308 ciclos
> duraron menos de 60 segundos y el `min_on_seconds` los habría eliminado todos.
> El caso grave es el deshumidificador, con **75% de sus ciclos por debajo del
> minuto** y 54% por debajo de los 30 segundos. Ver la sección de validación en
> [ROADMAP.md](./ROADMAP.md).

**Filtro de mediana** de 5 muestras sobre las lecturas: rechaza picos aislados
sin el retardo que agrega un promedio móvil.

**La banda muerta se deriva de la banda de la etapa**, así que "dentro de la banda
muerta" y "VPD en rango" son la misma afirmación y el control no trabaja para
ajustar algo que ya está en especificación. Hay un test que verifica esa
invariante para todas las etapas, temperaturas y humedades.

**Un solo origen de verdad para los rangos por etapa.** Estaban duplicados en
`vpd_math.py`, `db.py` y el template, y ya habían divergido.

**Timing con reloj monotónico.** Una Raspberry sin RTC toma la hora de NTP
después de arrancar, así que los deltas de reloj de pared pueden saltar horas.

**Apagado limpio.** El loop atiende SIGTERM/SIGINT y apaga todo al salir.

**El módulo del loop se puede importar sin hardware.** `adafruit_dht`, `board` y
`gpiozero` se importan de forma diferida, así que se puede testear en cualquier
máquina.

**`check_system` chequea el hardware real.** Verificaba dos BME280 por I2C, que
el código no usa: reportaba fallas en una instalación sana. Ahora chequea las
dependencias reales, Redis, la base de datos, que los pines estén libres y que no
haya loops duplicados.

**Validación de pines.** `gpio_pins_from_env()` rechaza dos salidas en el mismo
pin y valores no numéricos.

**`OUTPUTS` ya no es una foto del import.** Era un snapshot tomado antes de que
se pudiera leer cualquier override de entorno.

**El exterior faltante ya no se rellena con el interior.** Copiaba las lecturas
de adentro, dibujando una curva exterior falsa idéntica a la interior.

**322 tests** sobre la lógica pura, la capa de base de datos, los endpoints y el
comportamiento de los relés. No existían tests. Además configuración de `ruff` y
`pytest`, y `make test` / `make lint` / `make status`.

### Diferencias de documentación resueltas

- README describía dos **BME280 por I2C**; el código lee **DHT22 por GPIO** y/o un
  **ESP32 por HTTP**.
- README decía humidificador = pin 25 y ventilación = pin 7; el código usa 7 y 25
  al revés. **No se cambió el código** — es a eso que están conectados los relés —
  y se corrigió la documentación, con una advertencia para verificarlo.
- `scripts/check_i2c_bme.sh` chequeaba sensores que el proyecto no usa.

### Pendiente

- `ventilation` tiene pin y endpoint, pero ninguna lógica automática: hoy sólo
  responde a overrides manuales.
- `uv.lock` está en `.gitignore`. Para un deploy reproducible conviene commitearlo.
- La API no tiene autenticación. En una LAN de confianza puede estar bien, pero
  `POST /api/output-control` conmuta hardware.
