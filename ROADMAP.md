# Roadmap del Frontend

Plan para ir agregando features de forma incremental. Cada fase es independiente
y deja el sistema funcionando: se puede parar entre fases.

**Estado:** Fases 0, 1 y 2 completas. Antes de seguir con la 3, [validar con el cultivo real](#validación-con-el-cultivo-real-dogfooding).

| Fase | Qué | Estado |
|---|---|---|
| 0 | Refactor del front | ✅ |
| 1 | Contador de días por etapa | ✅ |
| 2 | Data más confiable | ✅ |
| 3 | Calendario de riego | ⬜ |
| 4 | Estadísticas más útiles | ⬜ |
| 5 | Mejores filtros y visualizaciones | ⬜ |
| 6 | Notificaciones configurables | ⬜ |
| — | **Validación con el cultivo real** | ⬜ ← conviene hacerla ahora |

---

## Diagnóstico del front actual

`autocann/web/templates/index.html` tiene **3013 líneas en un solo archivo**:

| Parte | Líneas |
|---|---|
| CSS inline | 1285 |
| HTML | 479 |
| JavaScript inline | 1233 |

Funciona y está bien diseñado visualmente (hay 7 media queries, así que lo
responsive está pensado). Los problemas son estructurales:

- **Nada se puede cachear.** El navegador vuelve a bajar 3013 líneas en cada
  carga, incluido todo el CSS que nunca cambia.
- **El JS no se puede testear.** No hay módulos, todo son globales y
  `onclick="..."` en los atributos.
- **Agregar tres features grandes acá lo vuelve inmanejable.** Este es el motivo
  real para hacer la Fase 0 antes que el resto.
- **Chart.js viene de un CDN.** Si la Raspberry o el navegador no tienen
  internet, los gráficos no cargan y no hay mensaje de error.
- **`VPD_RANGES` está duplicado** entre `vpd_math.py` y el template.

### Bugs encontrados (ver estado en cada uno)

| Bug | Estado |
|---|---|
| XSS: `grow.name` crudo en `innerHTML`; la API no tiene auth, así que es explotable desde la LAN y puede postear a `/api/output-control` | ✅ corregido |
| Un `0.0` real se muestra como `--` (temp. de hoja 0 °C, humedad objetivo 0) | ✅ corregido |
| "Sistema Activo" es un `<div>` estático: dice lo mismo con el backend caído | ✅ corregido |
| Backend caído = valores viejos en pantalla para siempre, sólo `console.error` | ✅ corregido |
| Polling de 3 s que sigue corriendo con la pestaña en segundo plano | ✅ corregido |
| Requests que se apilan si la Raspberry responde lento | ✅ corregido |
| Chart.js desde CDN sin fallback | ✅ corregido |
| Todo en un archivo de 3013 líneas | ✅ corregido |
| `VPD_RANGES` duplicado en el template | ✅ corregido |
| Modales sin cierre con Escape; alertas sin `aria-live` | ✅ corregido (falta atrapar el foco) |

---

---

## Fase 0 — Refactor del front (habilitador)

**Por qué primero:** todas las fases piden pantallas nuevas, estado nuevo y
bastante JS. Hacerlas sobre un archivo de 3013 líneas multiplica el costo de cada
una de las que vengan después.

**Restricción autoimpuesta: sin build step.** Módulos ES nativos, que andan en
todo navegador moderno. La Raspberry no debería necesitar Node ni npm para
levantar un dashboard.

```
autocann/web/
├── static/
│   ├── css/
│   │   ├── base.css          variables, reset, tipografía
│   │   ├── components.css    cards, botones, badges, modales
│   │   └── layout.css        grids y media queries
│   ├── js/
│   │   ├── api.js            fetch con timeout, AbortController, manejo de error
│   │   ├── util.js           escapeHtml, formato de fechas y números
│   │   ├── charts.js         creación y update de los 3 gráficos
│   │   ├── dashboard.js      hero cards, resumen, salidas
│   │   ├── grows.js          cultivos, etapas, modales
│   │   └── main.js           arranque y scheduler de polling
│   └── vendor/
│       └── chart.umd.min.js  vendorizado, el dashboard anda sin internet
└── templates/
    ├── index.html            layout + includes
    └── partials/
        ├── header.html
        ├── alerts.html
        ├── hero.html
        ├── summary.html
        ├── outputs.html
        ├── charts.html
        └── modals.html
```

**Tareas**

1. Mover CSS a `static/css/`, partir en tres archivos por responsabilidad.
2. Mover JS a módulos ES; reemplazar los 13 `onclick=` por `addEventListener`.
3. Partir el HTML en parciales de Jinja con `{% include %}`.
4. Configurar `static_folder` en `create_app()`, con cache-busting por
   `?v=<version>` tomada de `autocann.__version__`.
5. Vendorizar Chart.js y el plugin de anotaciones en `static/vendor/`.
6. `GET /api/config` que sirva rangos por etapa, nombres de etapas y duraciones
   esperadas, leídos de `vpd_math.py`. Elimina la última duplicación.
7. Modales: atrapar el foco, cerrar con Escape, `aria-live` en las alertas.
8. Un pequeño runner de tests de JS sin dependencias (`tests/js/`) o, más
   simple: mantener la lógica pura en `util.js` y testearla desde Python con un
   subproceso de `node` si está disponible, salteando si no.

**Entregable:** mismo dashboard, pixel por pixel, repartido en archivos
cacheables y con los bugs de la tabla cerrados.

### ✅ Hecho

`index.html` pasó de **3013 líneas a 33**:

```
templates/index.html          33   layout + includes
templates/partials/*.html    478   10 parciales
static/css/app.css          1298   extraído verbatim
static/js/*.js              1520   9 módulos ES
static/vendor/              234KB  Chart.js + plugin de anotaciones
```

**Una desviación del plan:** el CSS quedó en **un solo archivo** en vez de tres.
El orden de las reglas *es* la cascada, y repartirlas por concepto arriesga
regresiones visuales a cambio de nada — el objetivo real (fuera del template y
cacheable) se cumple igual. El archivo ya venía bien seccionado con banners.

Módulos, con la lógica pura aislada para poder testearla:

| Módulo | Qué hace |
|---|---|
| `util.js` | Funciones puras: escapado, formato de números y fechas. **Testeado** |
| `api.js` | Único punto de salida a la red: timeout, deduplicación, estado online/offline |
| `state.js` | Lo mutable compartido + la config que llega de `/api/config` |
| `modals.js` | Abrir/cerrar, para que analytics no tenga que importar grows |
| `charts.js` | Los tres gráficos; las instancias quedan privadas del módulo |
| `dashboard.js` | Lecturas, resumen, badges de sensores, tarjetas de relés |
| `grows.js` | Cultivos, etapas, lista |
| `analytics.js` | VPD score, anomalías, reporte semanal |
| `main.js` | Arranque, polling y **todo el cableado de eventos** |

Los 13 `onclick=` se reemplazaron por `data-action` + delegación en `main.js`.
Hacía falta por dos motivos: los módulos ES tienen scope propio, así que un
`onclick` inline no los alcanza; y construir handlers concatenando valores de la
base es justamente lo que produjo el XSS de la lista de cultivos.

**Verificación:** se capturó una huella del dashboard antes del refactor (348
elementos, cada valor en pantalla, datasets y puntos de los tres gráficos) y se
comparó después: **348 → 348 elementos y cero diferencias** en los valores. Se
probaron además los cuatro modales, el cierre con Escape y con el botón, el
selector de rango (25 → 169 puntos al pasar a 7d, con los dos selectores
sincronizados) y la lista de cultivos. Consola sin errores.

**Tests:** 10 de JavaScript sobre `util.js` con el runner nativo de node, más 6
de Python que verifican que no vuelvan los `onclick`, que el template no tenga
bloques `<style>`/`<script>` inline, que Chart.js no venga de un CDN, que todo
`import` resuelva a un `export` real y que los rangos por etapa no estén
hardcodeados en el front. `make test-js` los corre; si no hay node, se saltean.

### Lo que quedó pendiente de esta fase

- Atrapar el foco dentro de los modales (el cierre con Escape ya está).
- Reemplazar `confirm()` y `alert()` nativos (está en el backlog, #9).

---

---

## Fase 1 — Contador de días por etapa

**El problema de datos:** hoy no hay historial de etapas. `grows.stage` es un
único valor que `update_grow_stage()` sobreescribe, así que **la información para
contar días por etapa no existe**. Hay que empezar a registrarla.

### Esquema

```sql
CREATE TABLE stage_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    grow_id    INTEGER NOT NULL,
    stage      TEXT    NOT NULL,
    started_at INTEGER NOT NULL,          -- epoch
    datetime   TEXT    NOT NULL,          -- legible, zona local
    notes      TEXT,
    FOREIGN KEY (grow_id) REFERENCES grows(id)
);
CREATE INDEX idx_stage_events_grow ON stage_events(grow_id, started_at);
```

**Migración con backfill:** para cada cultivo existente, insertar un evento con
`started_at = grows.start_date` y la etapa actual. Es la mejor aproximación
posible con los datos que hay; de ahí en adelante el historial es exacto.
Idempotente, como la migración de columnas nullables que ya existe en `db.py`.

### Backend

- `create_grow()` y `update_grow_stage()` insertan un `stage_event`.
  `grows.stage` se mantiene como valor desnormalizado por compatibilidad.
- `get_stage_timeline(grow_id)` → `[{stage, started_at, ended_at, days}]`.
- `get_active_grow()` agrega `stage_started_at`, `day_in_stage`, `day_of_grow`.
- Duraciones esperadas por etapa en `vpd_math.py`, al lado de los rangos:
  `STAGE_EXPECTED_DAYS = {"early_veg": 21, "late_veg": 28, "flowering": 56, "dry": 10}`.
- `GET /api/grows/<id>/timeline`.

**Cuenta de días:** días de calendario en zona local, no bloques de 24 h. El día
en que arranca la etapa es el **día 1**: `(hoy_local - inicio_local).days + 1`.
Un cambio de etapa a las 23:00 no debe hacer que al otro día sea "día 1" otra vez.

### Frontend

- En la barra de cultivo: `Floración · día 12 de ~56` y `Cultivo · día 47`.
- Barra de progreso de la etapa, con el día esperado de cambio.
- En el modal de cultivos: una tira de timeline con las etapas y su duración.
- **Marcas de cambio de etapa en los gráficos históricos**, con el plugin de
  anotaciones que ya está cargado. Ver un cambio de etapa junto a la curva de VPD
  explica saltos que hoy parecen inexplicables.

### Tests

- Conteo de días a través de cambios de hora y de fin de mes.
- El backfill corre una sola vez y es idempotente.
- Un cambio de etapa a las 23:59 no reinicia el contador al minuto siguiente.

### ✅ Hecho

**Backend**

- Tabla `stage_events` + migración con backfill idempotente: cada cultivo
  existente recibe una etapa inicial estimada desde su `start_date`.
- `create_grow()` y `update_grow_stage()` registran la transición. Volver a
  setear la etapa en la que ya está **no** genera evento, así que un doble clic
  no reinicia el contador.
- `autocann/control/stages.py`: `day_number`, `build_timeline`,
  `estimated_remaining_days`, todo puro y sin I/O.
- `get_active_grow()` ahora trae `stage_started_at`, `day_in_stage`,
  `day_of_grow`, `stage_expected_days`, `stage_progress` y `estimated_end`. El
  cálculo está aislado: si falla, los contadores quedan en `None` en lugar de
  tumbar la función de la que depende el loop de control.
- `GET /api/grows/<id>/timeline`.
- `STAGE_EXPECTED_DAYS` en `vpd_math.py`, al lado de los rangos.

**Frontend**

- En la barra de cultivo: `día 14 de ~56` + `cultivo: día 63`, barra de
  progreso y fecha estimada de fin. El badge se pone ámbar cuando la etapa pasa
  su duración típica.
- **Marcas de cambio de etapa** sobre los tres gráficos, con el plugin de
  anotaciones. Sólo se dibujan las que caen dentro del rango visible, y se
  reposicionan al cambiar de rango.

**Un bug encontrado al implementarlo:** `chart.options` en Chart.js v4 es un
proxy resolver. El patrón habitual `plugins.annotation = plugins.annotation || {}`
le devuelve al proxy un valor que él mismo acababa de resolver, y su setter
recursa hasta reventar el stack (`RangeError: Maximum call stack size exceeded`),
dejando el gráfico sin actualizarse. Hay un test que impide que vuelva.

**Verificación en navegador:** contador en `día 14 de ~56`, `cultivo: día 63`,
progreso 25%, `fin estimado 23-nov`; marca de "Floración" presente en los tres
gráficos y zona óptima del VPD intacta; consola sin errores.

**Tests:** 27 de `stages.py` (incluyendo el cambio a las 23:59, cruces de mes y
año bisiesto, y el caso de dos cambios el mismo día), 7 de la capa de base de
datos y 2 guards de frontend.

---

---

## Fase 2 — Data más confiable

**Por qué va antes que las estadísticas:** cualquier estadística hereda la
calidad de lo que hay guardado. Hoy el histórico tiene huecos de información que
ninguna query puede recuperar después.

### Lo que falta hoy

**1. Se guarda una lectura instantánea, no el intervalo.** El loop lee cada 3
segundos pero persiste una muestra cada 5 minutos: de ~100 lecturas se guarda 1 y
se tiran 99. Un pico de 30 segundos puede quedar guardado como si fuera el valor
del período, o desaparecer por completo.

```sql
ALTER TABLE sensor_data ADD COLUMN temperature_min REAL;
ALTER TABLE sensor_data ADD COLUMN temperature_max REAL;
ALTER TABLE sensor_data ADD COLUMN humidity_min    REAL;
ALTER TABLE sensor_data ADD COLUMN humidity_max    REAL;
ALTER TABLE sensor_data ADD COLUMN sample_n        INTEGER;  -- lecturas agregadas
```

El loop acumula un agregador liviano entre escrituras y guarda avg/min/max/n. El
costo es una variable en memoria; la ganancia es poder ver la oscilación real,
que es lo que castiga a las plantas.

**2. No se guarda el contexto de la muestra.** Verificado: el loop publica estos
campos en Redis y ninguno llega a SQLite.

```sql
ALTER TABLE sensor_data ADD COLUMN stage          TEXT;
ALTER TABLE sensor_data ADD COLUMN indoor_source  TEXT;   -- esp32 | dht22_local
ALTER TABLE sensor_data ADD COLUMN control_action TEXT;   -- idle|humidify|dehumidify
ALTER TABLE sensor_data ADD COLUMN quality        TEXT;   -- ok|degraded|failsafe
```

Sin esto el histórico no puede responder preguntas básicas: *¿qué sensor produjo
esta lectura?*, *¿el control estaba actuando o estaba en failsafe?*, *¿esta
muestra es de floración o de vegetativo?*. Guardar `stage` por muestra además
evita tener que reconstruirlo desde el timeline en cada query.

**3. Calibración por sensor.** Con sensores baratos, dos unidades difieren entre
sí varios grados y porcentajes. Una tabla de offsets aplicada en la lectura:

```sql
CREATE TABLE sensor_calibration (
    sensor_id        TEXT PRIMARY KEY,   -- esp32_indoor, dht22_outdoor
    temperature_offset REAL NOT NULL DEFAULT 0,
    humidity_offset    REAL NOT NULL DEFAULT 0,
    updated_at       INTEGER NOT NULL,
    notes            TEXT
);
```

Se guardan **ambos** valores: el crudo y el corregido. Si después se recalibra,
el histórico crudo sigue siendo recuperable.

**4. Los huecos se dibujan como línea recta.** Si el sistema estuvo caído 6
horas, el gráfico une los dos extremos con una recta, que se lee como "la
temperatura bajó suavemente". Hay que cortar la línea: insertar un punto `null`
cuando el salto entre muestras supera 2× el intervalo esperado.

**5. Cross-check entre sensores.** Cuando haya dos sensores interiores (ver
[HARDWARE.md](./HARDWARE.md), recomienda SHT41), compararlos es el mejor detector
posible de un sensor que se está muriendo: una desviación sostenida entre dos
sensores que deberían leer lo mismo es una señal mucho más confiable que
cualquier umbral absoluto.

### Tests

- El agregador produce min ≤ avg ≤ max y un `sample_n` correcto.
- La corrección de calibración es reversible desde el valor crudo.
- La detección de huecos marca un corte y no interpola.

### ✅ Hecho

Los puntos 1 a 4. El 5 (cross-check entre sensores) queda para cuando haya un
segundo sensor interior — sin él no hay nada que comparar.

**`autocann/control/sampling.py`** — las tres piezas, puras y sin I/O:
`SampleAccumulator`, `Calibration` e `insert_gaps`.

**1. Resumen del intervalo.** El loop acumula cada lectura y escribe una fila con
avg/min/max/`sample_n`. Verificado end-to-end: una corrida real guardó filas de
**39 lecturas** con 4 °C de rango real, donde antes se habría guardado un único
valor instantáneo. Un intervalo sin ninguna lectura usable **no** escribe fila:
el hueco es el dato honesto.

**2. Contexto por muestra.** `stage`, `indoor_source`, `control_action` y
`quality` van en cada fila. `quality` se queda con lo peor del intervalo
(`ok` → `degraded` → `failsafe`), y marca `degraded` cuando el filtro de mediana
estuvo arrastrando lecturas viejas.

**3. Calibración.** Tabla `sensor_calibration`, `GET`/`POST /api/calibration`, y
offsets que el loop lee **una vez al arrancar** — una consulta cada 3 segundos no
compra nada. Se guardan los valores crudos además de los corregidos: la
corrección de humedad se satura en 0 y 100, así que invertirla no siempre
recupera lo que dijo el sensor. Hay un test que fija justamente ese caso.

**4. Huecos.** `insert_gaps` mete un marcador cuando el salto supera 2× el
intervalo esperado, y la API lo expone con `datetime` (para que tenga etiqueta en
el eje) y sin mediciones (para que cada serie dibuje un corte). Verificado en el
navegador con un corte de 4 h: `[26.41, null, 25.77]`.

**Además:** banda min–max dibujada sobre el gráfico de temperatura, que es el
motivo visual de guardar min/max. Se oculta sola en las filas viejas que no la
tienen.

**Un bug encontrado al implementarlo:** `last_db_save` arrancaba en el pasado
para guardar en la primera iteración. Con agregación eso escribía una fila que
decía representar cinco minutos a partir de una sola lectura, en cada arranque.

**Tests:** 29 de `sampling.py`, 5 de la capa de base de datos, 4 de los
marcadores de hueco en la API y 2 guards de frontend.

---

## Fase 3 — Calendario de riego

### Esquema

```sql
CREATE TABLE irrigation_schedules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    grow_id       INTEGER NOT NULL,
    name          TEXT    NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    mode          TEXT    NOT NULL,   -- interval | weekdays | per_stage
    interval_days INTEGER,
    weekdays      TEXT,               -- CSV 0-6, lunes = 0
    time_of_day   TEXT,               -- "HH:MM", sólo informativo
    volume_ml     INTEGER,
    nutrients     TEXT,
    notes         TEXT,
    FOREIGN KEY (grow_id) REFERENCES grows(id)
);

CREATE TABLE irrigation_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    grow_id     INTEGER NOT NULL,
    schedule_id INTEGER,              -- null = riego fuera de plan
    timestamp   INTEGER NOT NULL,
    datetime    TEXT    NOT NULL,
    volume_ml   INTEGER,
    ph          REAL,
    ec_ppm      REAL,
    nutrients   TEXT,
    notes       TEXT,
    source      TEXT NOT NULL DEFAULT 'manual',   -- manual | auto
    FOREIGN KEY (grow_id) REFERENCES grows(id),
    FOREIGN KEY (schedule_id) REFERENCES irrigation_schedules(id)
);
CREATE INDEX idx_irrigation_events_grow ON irrigation_events(grow_id, timestamp);
```

### Backend

- `autocann/control/irrigation.py`: **puro**. `next_due(schedule, last_event, today)`
  y `calendar_for_month(schedules, events, year, month)` → días planificados,
  hechos y perdidos. Toda la lógica de fechas testeable sin base de datos.
- `log_irrigation()`, `get_irrigation_history()`, `get_next_irrigation()`.
- Endpoints: CRUD de `/api/irrigation/schedules`,
  `POST /api/irrigation/events`, `GET /api/irrigation/calendar?year=&month=`,
  `GET /api/irrigation/next`.

### Frontend

- Grilla de calendario mensual: planificado / hecho / perdido / hoy.
- Card de "próximo riego" con cuenta regresiva.
- Botón rápido "Regué" que abre un formulario corto: volumen, pH, EC, notas.
- **Marcas de riego sobre el gráfico de humedad.** Regar sube la humedad; poder
  correlacionar riego con el salto de RH es genuinamente útil para entender la
  carpa.
- Historial con pH y EC en el tiempo.

### Integración con la Fase 6

La condición `irrigation_due` cierra el círculo: el calendario avisa solo. Como
las notificaciones van al final, el calendario se construye primero y la regla
`irrigation_due` se suma cuando llega la Fase 6 — por eso el esquema de
`irrigation_schedules` ya deja todo lo que esa regla necesita para calcular el
próximo riego sin tocar la tabla de nuevo.

### Fuera de alcance por ahora

**Riego automático con bomba.** El esquema ya lo contempla (`source = 'auto'`), y
hay una salida `ventilation` libre como precedente, pero automatizar agua
necesita decisiones de hardware (bomba, válvula, sensor de nivel, detección de
derrame) y un failsafe propio: una bomba trabada vacía un tanque en el piso.
Primero registro manual; la automatización es una fase aparte con su propia
revisión de seguridad.

---

---

## Fase 4 — Estadísticas más útiles

**Depende de la Fase 2** para las que necesitan min/max y `stage` por muestra.
Las de cálculo puro se pueden hacer antes.

### Lo más valioso primero: separar día y noche

La carpa se comporta como dos ambientes distintos según las luces. Hoy todas las
estadísticas promedian los dos juntos, lo que esconde exactamente los problemas
que importan: el VPD nocturno típicamente se va de rango y queda tapado por el
promedio diurno.

Necesita saber el fotoperíodo, que es configuración nueva:

```sql
ALTER TABLE grows ADD COLUMN lights_on_hour  INTEGER DEFAULT 6;
ALTER TABLE grows ADD COLUMN lights_off_hour INTEGER DEFAULT 24;
```

Con eso, **cada estadística existente se puede partir en día / noche / total**.
Es el cambio de mayor relación valor/esfuerzo de toda esta fase.

### Métricas nuevas

| Métrica | Por qué sirve | Necesita |
|---|---|---|
| **Punto de rocío** y **humedad absoluta** | Más accionable que la HR para decidir ventilación: dice si el aire de afuera sirve para secar | nada, es cálculo puro |
| **Índice de estabilidad** (desvío del VPD por hora) | Una carpa que promedia bien pero oscila es peor que una estable. El promedio solo no lo muestra | Fase 2 (min/max) |
| **Duty cycle de los relés** | % de tiempo encendido y ciclos por día. Mide directamente si el equipo quedó chico o grande, y si la protección anti-ciclado está haciendo efecto | `control_events`, ya existe |
| **Consumo estimado** | Duty cycle × watts del equipo. Un número concreto de lo que cuesta el cultivo | duty cycle + config de watts |
| **Inercia térmica** | Correlación y retardo entre temperatura exterior e interior: qué tan aislada está la carpa | nada |
| **VPD score por etapa** | Hoy el score es global. Por etapa dice en cuál se maneja peor | Fase 1 + Fase 2 |
| **Fecha estimada de cosecha** | Del timeline de etapas + duraciones esperadas | Fase 1 |
| **Comparación con el cultivo anterior** | Mismo día de etapa contra mismo día de etapa, no fecha contra fecha | Fase 1 |

Todo el cálculo va en `autocann/control/stats.py` como **funciones puras**, mismo
patrón que `humidity.py`: entran filas, salen números, se testea sin base de datos.

---

---

## Fase 5 — Mejores filtros y visualizaciones

### Filtros

Hoy sólo hay nueve botones de rango fijo (1h a 30d) y se aplican a todo por igual.

- **Rango de fechas libre**, con atajos ("este cultivo", "esta etapa", "últimos 7
  días").
- **Filtrar por etapa**: ver sólo floración.
- **Filtrar por día / noche** (Fase 4).
- **Filtrar por cultivo** y comparar dos cultivos superpuestos.
- Que el filtro elegido **se mantenga** al recargar (en la URL, así además es
  compartible: `?from=…&to=…&stage=flowering`).

### Visualizaciones

| Visualización | Qué agrega |
|---|---|
| **Banda min–max** en vez de sólo la línea del promedio | Muestra la oscilación real dentro de cada intervalo. Es el motivo visual para guardar min/max en la Fase 2 |
| **Banda objetivo dibujada** en el gráfico de humedad | El de VPD ya tiene la zona óptima anotada; el de humedad no muestra la banda de la etapa |
| **Heatmap hora × día** coloreado por VPD score | El reporte semanal ya calcula la distribución horaria; el heatmap es su forma natural. De un vistazo se ve "todas las madrugadas se va de rango" |
| **Marcas de eventos** sobre los gráficos | Cambios de etapa (Fase 1), riegos (Fase 3), encendido/apagado de relés. Explica saltos que hoy parecen inexplicables |
| **Zoom y brush** | Arrastrar para hacer zoom en un período |
| **Cortes en los huecos** | No dibujar una recta donde no hubo datos (Fase 2) |
| **Decimación** para rangos largos | Chart.js la trae; 30 días de datos crudos hoy se mandan enteros al navegador |
| **Exportar la vista actual** a CSV / PNG | Para compartir o analizar afuera |

---

---

## Fase 6 — Notificaciones configurables

**Decisión de arquitectura, la importante:** el loop de control **no manda
notificaciones**. Encola el alerta en SQLite y un dispatcher aparte la entrega.
El loop nunca puede bloquearse en I/O de red — es exactamente la lección de los
reintentos bloqueantes del DHT22 que ya corregimos.

```
loop de control → evalúa reglas (puro) → encola en `notifications`
                                              ↓
                              dispatcher (thread del backend)
                                              ↓
                          Telegram / webhook / dashboard
```

### Esquema

```sql
CREATE TABLE notification_rules (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    enabled           INTEGER NOT NULL DEFAULT 1,
    condition_type    TEXT    NOT NULL,   -- vpd_out_of_range, temp_above, humidity_below,
                                          -- sensor_offline, relay_stuck_on, irrigation_due,
                                          -- stage_change_due
    threshold         REAL,
    sustained_seconds INTEGER NOT NULL DEFAULT 300,   -- clave: evita el ruido
    stages            TEXT,               -- JSON; null = todas
    severity          TEXT    NOT NULL DEFAULT 'warning',
    cooldown_seconds  INTEGER NOT NULL DEFAULT 3600,
    channels          TEXT    NOT NULL,   -- JSON: ["telegram","dashboard"]
    quiet_hours_start INTEGER,            -- hora local 0-23
    quiet_hours_end   INTEGER
);

CREATE TABLE notifications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id         INTEGER,
    created_at      INTEGER NOT NULL,
    severity        TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    message         TEXT    NOT NULL,
    state           TEXT    NOT NULL DEFAULT 'pending',  -- pending|sent|failed|suppressed
    sent_at         INTEGER,
    error           TEXT,
    acknowledged_at INTEGER,
    FOREIGN KEY (rule_id) REFERENCES notification_rules(id)
);
CREATE INDEX idx_notifications_state ON notifications(state, created_at);
```

**`sustained_seconds` es lo que hace la diferencia entre útil e insufrible.**
"VPD fuera de rango" se cumple cien veces por día durante treinta segundos. Lo
que importa es "fuera de rango sostenido 30 minutos". Mismo razonamiento que la
banda muerta del controlador.

### Canales

| Canal | Esfuerzo | Nota |
|---|---|---|
| **Dashboard** | bajo | Centro de notificaciones con campanita y contador. Siempre activo |
| **Telegram** | bajo | Un bot, un `chat_id`, un POST. Llega al celular, gratis, sin servidor propio. **El recomendado** |
| **Webhook** | bajo | Genérico: Home Assistant, ntfy, Discord |
| Email SMTP | medio | Más configuración, más cosas que fallan |
| Web Push | alto | Necesita HTTPS y service worker. Dejar para después |

### Backend

- `autocann/control/alerts.py`: **función pura**, mismo patrón que
  `humidity.py`. `evaluate(rules, state, now, rule_state) -> list[Alert]`.
  Testeable sin red, sin base y sin hardware.
- `autocann/notify/` con `dispatcher.py`, `telegram.py`, `webhook.py`.
- Credenciales por variables de entorno (`AUTOCANN_TELEGRAM_TOKEN`,
  `AUTOCANN_TELEGRAM_CHAT_ID`), nunca en la base ni en el repo.
- Endpoints: CRUD de `/api/notification-rules`, `GET /api/notifications`,
  `POST /api/notifications/<id>/ack`, `POST /api/notifications/test`.

### Frontend

- Campanita en el header con contador de no leídas.
- Panel de centro de notificaciones.
- Modal de configuración con editor de reglas: condición, umbral, duración
  sostenida, canales, horario de silencio.
- Botón "Probar" por canal, para no descubrir que el token está mal el día que
  hace falta.

### Reglas por defecto razonables

| Condición | Umbral | Sostenido | Severidad |
|---|---|---|---|
| Sensor offline | — | 5 min | crítica |
| VPD fuera de rango | — | 30 min | warning |
| Temperatura alta | 30 °C | 10 min | crítica |
| Temperatura baja | 15 °C | 10 min | crítica |
| Humedad muy alta (hongos) | 75 % | 15 min | crítica |
| Relé pegado | — | 2 h encendido | warning |

---

---

## Validación con el cultivo real (dogfooding)

**Esto va antes que seguir agregando features.** Todo lo construido hasta acá se
probó contra datos sintéticos que yo mismo generé, y los datos sintéticos siempre
confirman lo que uno espera. El cultivo que está corriendo tiene historial real y
hardware real: es la única fuente que puede contradecirnos.

Puede además cambiar el orden de lo que falta. Si al mirar la data real resulta
que el problema dominante es otro, mejor saberlo antes de construir tres fases más.

### A — Leer la data del cultivo que hay

La base de producción vive en la Raspberry (`/home/autocann/Autocann/data/autocann.db`)
y nunca se miró con las herramientas nuevas.

**Traerla:**

- `make pull-data`: copiar la base de la Raspberry a `data/` local. Usar
  `sqlite3 .backup` o `VACUUM INTO` y no `scp` del archivo vivo — copiar un
  SQLite con WAL mientras se escribe da una base corrupta.
- Copia de solo lectura. Nada de este flujo debería poder escribir en producción.

**Mirarla:**

- `python -m autocann.cli.query_db stats` y `daily 30` sobre la data real: cuántas
  muestras hay, qué huecos tiene, desde cuándo.
- `GET /api/anomalies?hours=720` sobre el histórico real. Las anomalías que
  aparezcan son reales, no inventadas por mí.
- **VPD score real.** Si da muy bajo, hay que entender si es un problema de
  control o de que la banda por etapa no refleja cómo se cultiva acá.
- **Cuántos eventos de control hay por día** en `control_events`. Es la medición
  directa de cuánto estaban golpeando los relés *antes* del cambio, y queda como
  línea de base contra la cual comparar después.

**Validar que la migración no rompa nada:** correr las migraciones de las fases 1
y 2 sobre una copia de la base real antes de tocar producción. Son idempotentes y
hay tests, pero ninguno corrió sobre un archivo con meses de historial.

**Lo que el histórico viejo no tiene:** las filas anteriores a la fase 2 quedan con
`NULL` en min/max, etapa, origen y calidad. Las consultas ya lo contemplan
(`COALESCE`), pero conviene confirmarlo con la base real, que es la única con
mezcla de los dos formatos.

### B — Dogfooding del hardware

Correrlo en la carpa y dejar que la realidad opine.

**Lo primero, con la carpa parada:**

- ⚠️ **Verificar el mapa de pines contra el cableado.** El README decía
  humidificador=25 y ventilación=7, el código usa 7 y 25 al revés. Se corrigió la
  documentación y no el código, pero nadie miró el cable todavía.
- `make check` en la Raspberry: ahora chequea el hardware real y avisa si hay más
  de un loop corriendo.

**Después, dejarlo correr unos días y medir:**

| Qué medir | Con qué | Para qué |
|---|---|---|
| Conmutaciones por día | `control_events` antes vs. después | Confirmar que la protección de ciclado hace lo que dice la simulación (−39%) |
| Intervalos marcados `degraded` | columna `quality` | **Cuánto falla realmente el DHT22.** Hasta ahora es una impresión; ahora es un número |
| Oscilación real dentro del intervalo | `temperature_min/max` | Si la banda es ancha, el equipo está sobredimensionado o los mínimos son muy cortos |
| Huecos en el histórico | marcadores de hueco | Cuántas veces se cayó el sistema y por qué |

**Calibrar los sensores de verdad.** Hay un termómetro/higrómetro de referencia o
no lo hay; si lo hay, `POST /api/calibration` con el offset medido. Si no, al
menos poner los dos sensores juntos una hora y ver cuánto difieren entre sí — esa
diferencia ya es información.

**Ajustar los mínimos a cada equipo.** Los defaults (60 s encendido, 180 s apagado,
120 s de cambio) son un punto de partida razonable, no una medición. Para un
deshumidificador con compresor, `AUTOCANN_MIN_OFF_SECONDS=300` es más sano. Se
cambia por variable de entorno, sin tocar código.

**Recién después, decidir el hardware.** [HARDWARE.md](./HARDWARE.md) recomienda
cambiar el DHT22 por un SHT41 y mover el control a un ESP32. Los dos son
razonables en teoría; con el número de lecturas degradadas por día, dejan de ser
teoría. Si el DHT22 falla el 2% de las veces, el software ya lo está tapando bien
y no hay apuro; si falla el 30%, es la primera compra.

---

## Backlog (sin orden fijo)

| # | Feature | Por qué | Esfuerzo |
|---|---|---|---|
| 7 | **PIN o login** | La API conmuta hardware y hoy no tiene auth. Debería estar antes de exponer el dashboard fuera de la LAN | bajo |
| 8 | **PWA instalable** | Icono en el celular, shell offline, pantalla completa | bajo |
| 9 | **Reemplazar `confirm()` y `alert()`** | Hoy usa diálogos nativos bloqueantes; quedan feos y frenan la UI | bajo |
| 10 | **Diario del cultivo** con fotos | Notas fechadas que se cruzan con el timeline de etapas | medio |
| 11 | **Control de ventilación** | Tiene pin y endpoint pero ninguna lógica automática | medio |
| 12 | **CO₂** | Si se suma un SCD41 (ver HARDWARE.md) | medio |
| 13 | **Curvas objetivo por día de etapa** | VPD objetivo que se mueve con el día del cultivo en vez de una banda fija | alto |

---

---

## Orden recomendado

```
Fase 0 (refactor del front)        ← habilita todo lo demás
   └─> Fase 1 (días por etapa)
          └─> Fase 2 (data confiable)
                 └─> Fase 3 (calendario de riego)
                        └─> Fase 4 (estadísticas)
                               └─> Fase 5 (filtros y visualizaciones)
                                      └─> Fase 6 (notificaciones)

Fase 7 (PIN) — en cualquier momento, antes de exponer el dashboard fuera de la LAN

Validación con el cultivo real — intercalada acá, entre la fase 2 y la 3
```

**Por qué este orden:**

- **La 2 (data confiable) va temprano** porque las estadísticas y las
  visualizaciones dependen de datos que hoy no se guardan (min/max, etapa, origen
  del sensor). Hacerlas antes significa rehacerlas después. Y cada día que pasa
  es un día de histórico guardado sin esa información, que no se recupera.
- **El riego va antes que las visualizaciones** para que las marcas de riego
  sobre los gráficos salgan en la misma pasada que las de cambio de etapa, en
  lugar de volver a tocar los gráficos dos veces.
- **Las notificaciones van al final.** Además de ser lo que pediste, encajan
  bien ahí: para entonces los datos son confiables, existe el timeline de etapas
  y el calendario de riego, así que las reglas pueden alertar sobre todo eso de
  una sola vez (`irrigation_due` incluido) en lugar de ir ampliándose por partes.

## Principios que se mantienen

Lo que ya funcionó en el backend y conviene no abandonar:

1. **Lógica pura y testeable** en `autocann/control/`. Las decisiones (alertas,
   fechas de riego, conteo de días, estadísticas) son funciones sin I/O. Los
   tests corren sin hardware, sin Redis y sin red.
2. **Un solo origen de verdad.** Los rangos por etapa viven en `vpd_math.py` y
   llegan al front por `/api/config`. Nunca una cuarta copia.
3. **El loop de control no hace I/O de red.** Encola y sigue.
4. **Migraciones idempotentes** con backfill, como la de columnas nullables.
5. **Sin build step** en el front.

---

