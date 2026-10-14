# Changelog

## Un PIN adelante de la mitad que mueve relés

La API conmuta hardware y no pedía nada. Cualquiera en la LAN podía cerrar un
relé, pausar el controlador o borrar el plan de riego — backlog #7, y lo que
había que resolver antes de que esto fuera alcanzable desde algo que no sea la
red de la carpa.

Ahora hay un PIN, con tres decisiones que son más interesantes que el PIN mismo.

### Sin la variable, no cambia nada

`AUTOCANN_PIN` vacía o sin definir y la aplicación se comporta **exactamente**
como antes: nada pide nada. No es pereza, es la elección correcta entre dos
fallas. Un deploy que se olvida la variable, con el default al revés, deja al
dueño afuera de su propio tablero: no poder mirar la carpa es peor que ser
legible desde una red en la que ya estás parado. El que quiere el candado lo
pone; el que se olvida de ponerlo no pierde la carpa.

### Los nodos no pueden pasar por el PIN, y eso manda

`GET /api/outputs/desired`, `POST /api/sensor/indoor` y `POST /api/node/log`
quedan exentos. No es una excepción de conveniencia: **el firmware se graba a
mano, con un cable USB, parado en la carpa**. No le podés agregar una cookie ni
un header porque el servidor empezó a pedirlos.

Y el primero es el que de verdad decide esto. El nodo de relés lo pide cada 2
segundos y **se desenergiza solo a los 60 segundos sin un poll exitoso** — un
failsafe deliberado, para que una Raspberry muerta no deje un humidificador
prendido toda la noche. Para el nodo, un 401 y una Raspberry muerta son la misma
cosa: poner ese endpoint atrás del PIN **abre todos los relés de la carpa un
minuto después**, de madrugada, sin nada en pantalla que lo explique. Los dos
POST del sensor son más silenciosos pero no inofensivos: sin lecturas el lazo
declara el sensor viejo a los 60 segundos y entra en failsafe, que también
suelta los relés.

El motivo está escrito **al lado de la lista**, en `autocann/web/auth.py`, no en
este changelog: alguien va a ver tres endpoints sin autenticar y va a querer
ordenarlos. Hay un test que falla si alguno se renombra y pierde la exención sin
que nadie se entere.

Lo que hace que esto sea aceptable y no un agujero es que ninguno de los tres
conmuta nada. El primero es de sólo lectura. El segundo toma dos números dentro
de rangos físicos y un `sensor_id` contra un patrón estricto antes de que sea
parte de una clave de Redis. El tercero mete líneas de log en un buffer
circular con tope. Lo que hay que proteger es la mitad de la API que mueve
relés, y esa está cubierta: relés, pausa, cultivos, plan de riego, calibración
y franjas de silencio.

### La sesión dura 30 días

El tablero se usa desde el celular, parado en la carpa, con las manos mojadas.
Un PIN por día es cómo termina escrito con fibrón al lado de la puerta. Cookie
firmada, 30 días, y la clave de firma se deriva del PIN salvo que
`AUTOCANN_SECRET_KEY` diga otra cosa — si fuera aleatoria por arranque, cada
deploy mandaría al teléfono y a la tablet de vuelta al login. Derivarla del PIN
no regala nada: quien pueda falsificar una cookie con eso ya sabe el PIN, que es
toda la autorización que hay.

La sesión guarda una **huella del PIN**, no un flag, así que cambiarlo cierra
las sesiones abiertas con el anterior. Hay un botón para cerrar sesión en
**Sistema → Sesión**, que aparece sólo cuando hay PIN configurado. Y después de
10 intentos fallidos esa IP espera 5 minutos: cuatro dígitos son diez mil
pruebas, que un script en la misma LAN hace en segundos. No vuelve fuerte al
PIN; vuelve lento al que lo adivina.

### Dos detalles del front

El login es una **página aparte**, no un modal sobre el tablero: el tablero
dispara una docena de pedidos apenas carga y todos contestarían 401 atrás del
diálogo. Y `api.js` recarga la página ante un 401, en vez de dejar en pantalla
números de antes de que la sesión venciera mientras la consola se llena de
errores.

## El histórico es de un cultivo, no de una carpa

Las ventanas de 30 y 90 días filtraban por tiempo y se llevaban puesto lo que
hubiera atrás. Con el cultivo de prueba por cerrarse y uno real por arrancar,
eso significa que el promedio de 30 días del cultivo nuevo iba a venir mezclado
con semanas de carpa vacía — y la curva iba a ir de la última muestra de uno a
la primera del otro **con una línea recta**, como si la carpa hubiera seguido
andando sola.

### El default es el cultivo activo, y se defiende solo

Todo lo demás en pantalla ya está dicho en el contexto de un cultivo: la barra
de arriba dice cuál es, la etapa, el día dentro de la etapa, la banda objetivo
contra la que se mide el VPD. Un promedio de 30 días que incluye el cultivo
anterior es un número **etiquetado con el contexto equivocado**: dice
"Floración · día 25" arriba de una media que incluye dos meses de otra cosa. La
alternativa — que el default sea "todo" — arruina la vista que se usa todos los
días para abaratar una que se usa una vez por cultivo.

Así que "todos los cultivos" **sigue estando y hay que pedirlo**: `grow_id=all`
en la API, una opción en el selector nuevo de Estadísticas. Cruzar diez meses de
carpa vacía con un cultivo real es una decisión, no algo que pase.

### Lo que estaba roto no era que no filtrara

La capa de base *sí* resolvía el cultivo activo cuando no le pasaban ninguno. El
problema era el camino de abajo: **si no había cultivo activo, no filtraba por
nada** y contestaba con todos. Y ese caso no es raro, es exactamente el momento
que importa — los minutos (o los días) entre cerrar un cultivo y crear el
siguiente. La única consulta que alguna vez devolvía todos los cultivos era la
que lo hacía sin querer.

Ahora `None` significa "el cultivo activo" y, sin uno, la respuesta es vacía.
Vacío es la verdad ahí: no hay cultivo del que informar. `/api/sensor-history`
además acepta `grow_id`, que antes no aceptaba: no había forma de mirar un
cultivo cerrado.

**El detector de anomalías es la excepción deliberada.** Sin cultivo activo no
se queda callado: contesta "no hay datos en las últimas 24 horas", que es
exactamente lo que está pasando, porque el lazo no está guardando nada. Leer las
últimas filas de un cultivo cerrado como "la carpa está bien" es lo contrario de
para lo que existe un detector. En la otra dirección, `/api/health` pregunta a
propósito por **todos** los cultivos: ahí la pregunta es si la instalación sigue
escribiendo, que es sobre la Raspberry y no sobre un cultivo, y acotarla al
activo haría que "el histórico se cortó" apareciera justo mientras no hay nada
roto.

### Los huecos no pueden unir dos cultivos

Los marcadores de corte miraban sólo el tiempo, y entre el final de un cultivo y
el arranque del otro suele haber cinco minutos: ningún umbral de outage lo
agarra. `insert_gaps` ahora corta también cuando cambia el cultivo, con el punto
marcado como `series_change` para que se lea como lo que es — un cambio de
cultivo, no un corte de datos.

Y el bucket de agregación se agrupa por cultivo además de por intervalo. Una
hora que contiene el promedio de dos carpas distintas es un número sobre nada.

### Las estadísticas por etapa se niegan a promediar cultivos

`/api/vpd-score` y `/api/weekly-report` rechazan `grow_id=all` en vez de
contestar algo. El score es el porcentaje de tiempo dentro de la banda de **una
etapa**, y cada cultivo pasó por etapas distintas: un solo porcentaje sobre
todos puntúa lecturas contra bandas a las que nunca estuvieron sujetas. El
cartel lo dice en pantalla en lugar de dejar el número en `--%` sin explicación.

De yapa, la comparación "contra la semana anterior" del reporte quedó acotada
también: la primera semana de un cultivo ahora compara contra nada y no reporta
tendencia, que es la verdad, en vez de medir una tendencia contra la carpa
vacía.

## El sensor de afuera dejó de frenar el lazo

El DHT22 exterior se leía **adentro del lazo de control**, en la misma llamada
que produce la lectura con la que se decide. Un DHT22 que no contesta cuesta
`DHT_READ_ATTEMPTS` intentos con un `sleep` de dos segundos entre ellos, así que
un sensor exterior muerto le comía al lazo cerca de dos segundos de cada ciclo
de tres. Durante esos dos segundos nadie estaba mirando la carpa: ni se evaluaba
la humedad, ni se refrescaba el estado deseado que el nodo de relés necesita
escuchar — y ese nodo se desenergiza solo a los 60 segundos de silencio.

Lo peor es por qué pasaba: **el exterior no alimenta ninguna decisión**. No entra
en el VPD, no entra en la banda de la etapa, no entra en el failsafe. Se grafica.
El lazo crítico se estaba frenando por un número decorativo.

Ahora lo lee un hilo de fondo, una vez por minuto, y el lazo toma el último valor
cacheado. La elección entre "hilo con caché" y "leerlo cada tantos ciclos" se
resolvió por el caso peor: leerlo cada N ciclos sigue poniendo la espera adentro
del lazo, sólo más espaciada, y el ciclo que le toca es igual de ciego que antes.
Un hilo saca la espera del camino de una vez y para siempre.

**La caché envejece y se apaga.** Está ahí para que el lazo no se bloquee, no
para poder mostrar un número viejo como si fuera de ahora. Cada lectura guarda el
instante monotónico en que se tomó, y pasados cinco minutos — cinco ciclos del
lector — deja de servirse: los campos salen en `None` y la tarjeta vuelve a
`--`. Una lectura fallida sola, en cambio, **no** borra la caché: un checksum
perdido no cambió el clima afuera.

**Sin lectura exterior, los campos van en `None`.** Sigue valiendo lo de siempre
y por el mismo motivo: copiar ahí los valores de interior dibujaba un exterior
falso, idéntico al interior, en el gráfico de comparación. La fila guarda crudo y
calibrado como antes.

**El estado `outdoor` ahora dice la verdad sobre la lectura, no sobre el objeto.**
Antes contestaba `ok` si el objeto `DHT22` existía, así que un sensor que arrancó
bien y después se quedó mudo durante horas mantenía un badge verde arriba de un
gráfico vacío. Ahora `ok` significa "hay una lectura fresca", y cuando no la hay
el error distingue tres casos distintos: nunca se inicializó, no contestó, o la
última quedó vieja.

**El reinicio de sensores se partió en dos.** El lazo, en failsafe, reintentaba
crear los dos DHT22; el exterior ahora es del hilo que lo lee, y dos hilos
cerrando y reemplazando el mismo dispositivo por debajo del otro es una carrera
que no hace falta tener. `init_indoor_dht22()` es del lazo, `init_outdoor_dht22()`
del lector, y el exterior tampoco es el motivo por el que el lazo está en
failsafe.

## ¿Anda el sensor? Una sola respuesta

Tres fuentes contestaban esa pregunta y ninguna sabía de las otras: el badge del
hero salía de `sensor_status` — la opinión del lazo de control sobre el primario
—, cada tarjeta de sensor de la antigüedad de su propia lectura en Redis, y el
cartel de anomalías de la antigüedad de la última fila en SQLite.

Con el sistema en pausa las tres decían cosas distintas **al mismo tiempo**, en
la misma pantalla: un *"Último dato hace 238 minutos — sensor posiblemente
desconectado"* en rojo, arriba de dos tarjetas verdes que decían "OK", con los
dos sensores posteando cada diez segundos sin fallar uno. Y el badge verde del
hero convivía con un VPD en `--`, porque en pausa el lazo no calcula VPD: el
badge estaba hablando del sensor y la tarjeta mostraba lo que el lazo no había
computado, pero nadie que mire la pantalla tiene por qué saber eso.

El veredicto se calcula ahora **una vez**, mirando las tres fuentes juntas, y se
sirve entero en `GET /api/health`. Dos elementos de la pantalla no pueden
discrepar si leen del mismo objeto; esa es toda la idea.

**Cuatro estados por sensor, no dos.** "Nunca posteó" y "dejó de postear" se ven
iguales en una tarjeta que sólo sabe ok/error, y mandan a lugares distintos: la
primera al id del firmware y al cableado, la segunda a la red y al log del nodo.
El cuarto es "postea, pero sospechoso" — contesta, a tiempo, en rango, y el
detector de deriva lo marcó. Ése es el que mejor esconde un badge verde, y es
justo la falla para la que existe el testigo. Cada tarjeta dice además, en
castellano, qué le pasa y qué mirar, y **apaga sus propios números cuando son
viejos**: mostrar 21.6 °C en naranja fuerte cuando la lectura es de hace cuatro
horas es, en sí mismo, decir algo falso.

**El lazo y el histórico dejaron de ser estados del sensor.** Son dos renglones
aparte en el panel, porque la carpa puede quedarse sin control con los sensores
perfectos, y el histórico puede dejar de escribirse con todo lo demás andando.

**Cada cartel habla sólo de lo suyo.** El badge del hero reporta el lazo y el
sensor que controla, que son los dos de los que depende el número que esa tarjeta
muestra — un hueco en el histórico es un problema real y no es de esa tarjeta. El
cartel rojo de arriba de todo aparece sólo por sensores y por el lazo, porque el
histórico ya lo cuenta el de anomalías y dos cajas rojas pegadas diciendo lo
mismo es la forma más rápida de que alguien deje de leer las dos.

**Las anomalías se releen contra lo que está pasando ahora.** `detect_anomalies`
sigue mirando la base, que es el lugar correcto para buscar un hueco en el
histórico y el equivocado para buscar un sensor muerto: los dos son la misma
cantidad de filas. Lo que encuentra pasa ahora por el estado real antes de salir
del backend — no sólo en el banner, así que cualquier consumidor de
`/api/anomalies` ve lo mismo:

- **Con el sistema en pausa el hueco es esperado y se descarta.** La barra de
  pausa ya lo dice, en las palabras correctas. Esto importa más desde que la
  pausa no vence sola: antes el número tenía techo de ocho horas, ahora crece
  indefinidamente y el cartel rojo se vuelve permanente apenas alguien pausa y
  se va.
- **Con los sensores posteando, el que no está guardando es el lazo**, y eso es
  lo que dice el mensaje. Un sensor vivo no se llama más "desconectado".
- **Sin filas y sin lecturas se deja intacto**: ahí el sensor sí está
  desconectado.

El color de la tira se recalcula al filtrar. Dejar `status: "critical"` con todos
los items críticos descartados mantenía el cartel rojo con nada rojo adentro.

## La pausa dura hasta que alguien la saca

Pausar el controlador vencía solo a las ocho horas. El argumento era una falla
real — una pausa que nadie recuerda deja la carpa sin control sin que nada avise
— pero el remedio apuntaba al lado equivocado. Una pausa que termina por reloj
**cierra los relés en un momento que nadie eligió**, y el motivo por el que uno
pausa es meter mano en la carpa: el timer dispara con las manos adentro. Lo que
esa preocupación pide de verdad es que el estado sea imposible de pasar por
alto, y eso se dice en pantalla en vez de hacérselo a los relés.

Dura hasta que se la saca, y el encabezado lo dice todo el tiempo que dure.

**El estado se mudó a la base y Redis quedó de espejo.** Redis es memoria:
reiniciarlo, o reiniciar la Raspberry, devolvía la carpa al controlador en
silencio — la misma sorpresa que las ocho horas, con menos aviso todavía. El
lazo sigue leyendo Redis en cada ciclo porque es el camino rápido, y repone el
espejo desde la base en el intervalo de settings, **antes** de evaluar la pausa,
para que un Redis que volvió vacío no alcance a meter un ciclo de control
automático. Un Redis *inalcanzable* sigue leyéndose como "en control": sin él
tampoco se pueden mover los relés a mano, así que quedarse en pausa sin que
nadie pueda intervenir no le sirve a nadie. El payload dice `since` y
`elapsed_seconds` donde antes decía `until` y `remaining_seconds`, porque ya no
hay vencimiento hacia el cual contar y el número que importa es hace cuánto que
la carpa viene sola.

**El control está donde está el estado.** "Sistema Activo" era un cartel
decorativo en el encabezado mientras el botón que lo cambiaba era un link
fantasma a mitad del panel — alcanzable sólo desde la única vista que lista los
relés, que no es la vista en la que uno está cuando quiere frenar la carpa.
Ahora es un botón: informa el estado y apretándolo lo cambia. "Sin conexión" le
gana a "en pausa", porque mientras el backend no contesta lo que dijo el último
payload sobre la pausa es una suposición, y el botón se deshabilita en vez de
parecer vivo.

Registrar un riego es la otra cosa que se hace desde cualquier lado, así que es
la otra cosa del encabezado. El panel tenía una tarjeta entera para eso — días
desde el último riego, dosis de hoy, un link — que era una copia peor de la
pestaña Riego en la única pantalla pensada para leerse y no para usarse. Queda
la parte que sí es un llamado a actuar: un badge en el botón, y sólo a partir
de los cuatro días, porque un badge siempre encendido es mueble.

**Dos cosas que aparecieron probándolo.** En el teléfono el panel scrolleaba de
costado: un track de grilla no baja del `min-content` de su contenido, y el
contenido era un canvas que Chart.js había dimensionado para un escritorio, así
que la columna quedaba en 962 px dentro de una pantalla de 375. `min-width: 0`
deja encoger el track, que es lo que permite que Chart.js se entere y redibuje.
Y el tiempo transcurrido salía "--" durante el primer minuto: `formatDuration`
describe tiempo *restante*, así que lee el cero como "sin duración", y cero
segundos transcurridos es una respuesta válida que significa que acabás de
apretar el botón.

## El log del nodo, sin entrar a la carpa

El ESP32 imprime por el puerto serie exactamente lo que está mal — un DHT22 que
no contesta, un POST que volvió 400, el WiFi que se cayó, un arranque por
brownout — y nada de eso se podía ver. Leer ese puerto significa entrar a la
carpa con una notebook y **desenchufar el nodo de su fuente para enchufarlo al
USB**, lo que lo reinicia y borra justo el estado que se quería mirar.

Así que el nodo manda su propio log a `POST /api/node/log` cada 30 s, y se lee en
**Sistema → Nodos ESP32** junto con su firmware, su IP, la señal, el motivo del
último arranque, las reconexiones de WiFi y los contadores de lecturas y de POST
**por sensor** — que es lo que distingue un DHT22 con un cable flojo de uno sano:
una falla suelta cada tanto es normal en estos sensores, una de cada tres no.

Lo que se encola son **eventos**, no cada lectura: fallas, recuperaciones,
arranques y un latido cada cinco minutos. La lectura de cada ciclo sigue saliendo
sólo por el serie, porque encolarla llenaría la cola de números normales y
tiraría afuera justo la línea que explica la falla. Con 300 líneas guardadas, eso
es alrededor de un día de historia en vez de media hora.

Dos decisiones que se notan cuando algo se rompe de verdad:

- **Las líneas no se descartan hasta que el backend las acepta.** Por eso una
  caída de WiFi se puede leer después: las líneas que la cuentan sobreviven a la
  caída que impidió mandarlas. Si la cola se llena se tira la más vieja — con el
  nodo incomunicado hace rato, lo último que pasó explica mejor el estado actual
  que lo primero — y se informa cuántas se perdieron.
- **La hora la pone la Raspberry.** El ESP32 cuenta milisegundos desde que
  arrancó, que es el único reloj que tiene; convertirlos a hora de pared lo hace
  el único que sabe las dos cosas, cuánto hace que pasó y cuándo llegó el
  reporte. Sin eso el log es una columna de números que no se puede alinear con
  nada más de lo que pasó en la carpa.

El endpoint no tiene autenticación, como el de sensores que tiene al lado, así
que todo lo que entra viene acotado: el nombre del nodo se valida antes de entrar
en una clave de Redis, las líneas se truncan y se limitan por reporte, y el log
guardado tiene tope. `secrets.h` no cambió: la URL se deriva de `API_URL`, así que
un nodo ya grabado sólo necesita el sketch nuevo.

## Modo prueba

Trabajo de banco con la carpa vacía: **el lazo deja de decidir y no se guarda
nada**. Los relés quedan donde se los ponga desde el dashboard; ni las muestras
ni los eventos de control llegan a SQLite, porque una sesión moviendo relés a
mano no es historia del cultivo.

> [!IMPORTANT]
> Lo que sigue describe el diseño original, con vencimiento a las 8 horas.
> Después se reemplazó: ahora la pausa dura hasta que alguien la saca y vive en
> la base. Ver «La pausa dura hasta que alguien la saca» más arriba.

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
