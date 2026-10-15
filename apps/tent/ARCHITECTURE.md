# Arquitectura - Autocann

Este repo arrancó como un conjunto de scripts y con el tiempo fue creciendo. Para hacerlo mantenible, la lógica se movió a un paquete Python llamado `autocann/` y el directorio `scripts/` quedó como **entrypoints finitos** (wrappers) para correr cosas con `uv run`.

## Principios

- **`autocann/`**: código reusable (db, web, control, hardware). Importable desde cualquier lado.
- **`scripts/`**: solo shell scripts (`.sh`) para iniciar/verificar servicios.
- **Config centralizada**: pines/redis vía `autocann/config.py` + variables de entorno.
- **`control/` es puro**: nada de lo que vive ahí toca la base, el reloj ni el
  hardware salvo que se lo pasen por parámetro. Por eso la decisión de control
  se testea sin Redis, sin SQLite y sin carpa.

## Estructura (alto nivel)

### Base

- `autocann/config.py`: configuración (Redis, pines GPIO locales, pines del nodo
  remoto, tuning del control, sensores interiores, modos) con overrides por env
  vars. Valida pines compartidos e ids de sensor antes de interpolarlos en
  claves de Redis.
- `autocann/paths.py`: paths del proyecto (`data/`, `autocann/web/templates/`, etc).
- `autocann/time.py`: timezone común.
- `autocann/redis_pool.py`: el cliente de Redis de los procesos largos.
  Pool con techo y socket con timeout: los defaults de redis-py no tienen
  ninguno de los dos, y un backend que vive semanas los necesita.
- `autocann/db.py`: capa de persistencia SQLite + API de "grows". WAL,
  `busy_timeout` y migraciones idempotentes con backfill.

### `control/` — lógica pura, sin I/O

- `vpd_math.py`: funciones puras de cálculo (VPD/targets) y **único origen de
  verdad** de los rangos por etapa y de los días esperados de cada una.
- `humidity.py`: la decisión de control. Banda muerta, tiempos mínimos de
  encendido/apagado/cambio de sentido y failsafe por dato viejo.
- `quiet.py`: las franjas de silencio, como restricción de la **decisión** y no
  del relé — así los tiempos mínimos del controlador siguen describiendo un
  equipo que de verdad estuvo apagado.
- `stages.py`: la línea de tiempo de etapas, contada en días calendario de la
  zona local y no en bloques de 24 h.
- `sampling.py`: el acumulador avg/min/max del intervalo, la calibración
  reversible por sensor y los marcadores de hueco de los gráficos.
- `drift.py`: detección de deriva silenciosa — el modo de falla que el filtro de
  mediana y el failsafe por dato viejo no ven, que es un sensor degradado
  informando un número plausible y estable pero equivocado. Sólo avisa: nunca
  desenergiza.
- `feeding.py`: la aritmética de la grilla de fertilización. Semanas contadas
  dentro de la etapa, como se imprimen los feed charts.

### `hardware/`

- `outputs.py`: definición de outputs (nombre/label/pin/redis_key), el canal de
  override manual y `pin_sources()`, que resuelve si el pin que importa es el de
  la Raspberry o el del nodo remoto. Libre de Flask y de imports de Raspberry,
  así que lo pueden importar los dos lados.

### `web/`

- `app.py`: Flask app + las 30 rutas del núcleo. `auth.py` y `diagnostics.py`
  registran las otras 6 sobre la misma app: 36 en total, documentadas en
  `USAGE.md`.
- `auth.py`: el PIN opcional de `AUTOCANN_PIN`, con tres endpoints exentos a
  propósito y para siempre. Sin la variable, el comportamiento es el de siempre
  y nada pide autenticación.
- `diagnostics.py`: "¿anda la instalación?" en una sola respuesta
  (`/api/health`), más el log que los nodos postean. Existe porque el panel
  contestaba esa pregunta desde tres fuentes que podían contradecirse.

### Front

- **Design system "Grafito"**: `static/css/tokens.css` (colores en dos temas,
  Grafito y Noche; tipografía, espacio, radios) y `static/css/components.css`
  (las clases `ac-*`). Son la copia en código del design system documentado
  como artifact; si cambia uno, cambia el otro. `app.css` mapea sus variables
  viejas a los tokens, y cada vista tiene su hoja en `static/css/views/`.
- Navegación: barra lateral colapsable (`partials/sidebar.html`, `sidebar.js`)
  con la carpa, las vistas y el estado del lazo; en el celular se parte en una
  barra arriba y la barra de pestañas abajo.
- `autocann/web/static/js/`: 25 módulos ES, sin bundler. Los nuevos con lógica
  pura y tests: `status.js` (la frase del panel), `vpdchart.js` (la carta de
  VPD, cuya franja sale de `/api/vpd-band` con la misma cuenta que el lazo),
  `logbook.js`, `grow-model.js`, `stats-math.js`, `theme.js`.
- `autocann/web/templates/`: `index.html` y `login.html`, más 12 parciales.
- Íconos: [Lucide](https://lucide.dev) como sprite SVG incrustado en la página
  (`templates/partials/icons.html`), con `icon()` en Jinja y en `util.js`. Nada
  de emoji: la Raspberry no tiene fuente de emoji y se veían como cuadrados
  vacíos. `make test` falla si vuelve uno o si se pide un ícono que no está en
  el sprite.
- Fuentes (IBM Plex Sans y Mono) servidas desde `static/vendor/fonts/`, igual
  que Chart.js: la página no depende de internet para nada.
- `tests/`: suite de pytest. La lógica de control no necesita hardware ni Redis.
  Los tests de JS corren con `node --test tests/js/` y también desde pytest.

## Entry points

- `python -m autocann.cli.backend` (server web)
- `python -m autocann.cli.vpd` (control VPD)
- `python -m autocann.cli.check_system` (healthcheck)
- `python -m autocann.cli.query_db` (tools de consulta)
- `python -m autocann.cli.inspect_db` (reporte sobre una base traída de producción)
- `python -m autocann.cli.compact_db` (compactar el `control_events` viejo)

## Deuda conocida

- `autocann/cli/vpd.py` tiene ~1480 líneas y es lo único que queda sin partir:
  además del loop, ahí viven el acceso a Redis, la lectura de sensores, el
  driver de relés y la orquestación. Las piezas puras ya están todas en
  `control/` y tienen tests; lo que falta es que el loop quede como
  orquestación y nada más. Ojo que es el archivo que **crece** en cada feature,
  así que la deuda se agranda sola.
- `cli/inspect_db.py` no tiene tests, y el `main()` del loop no lo ejecuta
  ninguna prueba (sí sus piezas).
