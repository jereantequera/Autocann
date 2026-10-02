# Recomendación de Hardware

Para armar esto de cero y dejar de renegar con interferencias y lecturas malas.

## El diagnóstico

Tres cosas del diseño actual generan la mayoría de los problemas:

1. **El DHT22.** Protocolo de un solo hilo, bit-banged desde Python, sin
   corrección de errores. Es lento, impreciso (±2–5% RH), deriva con el tiempo y
   es muy sensible al largo del cable y al ruido eléctrico. Toda la lógica de
   reintentos del código existía para tapar esto.

2. **Relés mecánicos conmutando cargas inductivas sin snubber.** Un
   deshumidificador con compresor y un ventilador generan arco en los contactos
   al abrir. Ese arco es un pulso de banda ancha que se acopla justo al cable del
   sensor que pasa al lado. Además la bobina del relé, alimentada desde el mismo
   5 V que la lógica, mete picos en la alimentación de la Raspberry.

3. **Linux haciendo control en tiempo real.** La Raspberry no es un sistema de
   tiempo real: bit-banguear el DHT22 desde userspace con el GIL de Python da
   jitter de timing, y eso solo ya produce lecturas corruptas. Y si el kernel se
   cuelga o se corrompe la SD, los relés quedan como quedaron.

El patrón de fondo: **el lazo de control crítico depende de la pieza más frágil
del sistema.**

## La recomendación principal

**Mover el lazo de control al microcontrolador, al lado de los sensores y los
relés. La Raspberry queda como dashboard e histórico.**

```
┌─────────────────────────────────────┐        ┌──────────────────────────┐
│ ESP32 (en la carpa)                 │        │ Raspberry Pi (afuera)    │
│                                     │  MQTT  │                          │
│  SHT41 ──I2C(corto)──┐              │ ◄────► │  Dashboard Flask         │
│  SHT41 ──I2C(corto)──┤              │  WiFi  │  SQLite histórico        │
│                      ├─ lazo de     │        │  Analítica / reportes    │
│  Relés opto-aislados ┘  control     │        │                          │
│                         + failsafe  │        │  Puede caerse sin        │
│                                     │        │  consecuencias           │
└─────────────────────────────────────┘        └──────────────────────────┘
```

Por qué es el cambio más grande en confiabilidad:

- Los cables de I2C quedan de 10 cm en vez de 3 m. El problema de interferencia
  desaparece casi por completo, no se mitiga.
- El ESP32 sigue controlando si se cae la WiFi, si muere la SD de la Raspberry o
  si se te ocurre reiniciar el backend.
- Desaparece toda la clase de bugs de "dos procesos Python peleándose el GPIO".
- El timing del sensor lo hace un MCU dedicado, sin scheduler de por medio.

Software recomendado para el ESP32: **ESPHome**. Es YAML declarativo, trae
histéresis y tiempos mínimos de ciclo ya resueltos, actualiza por OTA y habla
MQTT nativo. Si preferís escribirlo a mano, también está bien: lo importante es
dónde corre, no en qué está escrito.

---

## Lista de componentes

### Sensores — reemplazar el DHT22

| Sensor | Precisión RH | Bus | Cuándo usarlo |
|---|---|---|---|
| **SHT41 / SHT45** | ±1.5% | I2C | **La opción por defecto.** Barato, estable a largo plazo |
| SHT31-D | ±2% | I2C | Muy común, breakouts baratos, dirección configurable |
| SCD41 | ±6% (+ CO₂) | I2C | Si querés medir CO₂, que importa en floración |
| BME280 | ±3% | I2C | Aceptable; el RH deriva más que un Sensirion |
| ~~DHT22 / AM2302~~ | ±2–5% | 1-wire | **Evitar** |

Los Sensirion (SHT4x) son la diferencia más grande por peso: la estabilidad a
largo plazo es lo que hace que no tengas que recalibrar cada dos meses.

**I2C es de corto alcance** (menos de ~1 m confiable). Para tiradas largas, en
orden de preferencia:

1. **Poner un ESP32 al lado del sensor** y mandar la lectura por red. Es lo que
   ya hacés con el sensor interior — buen instinto, extendelo a todos.
2. **RS-485 / Modbus** (ej. XY-MD02, que internamente es un SHT). Señalización
   diferencial, inmune justo al ruido que te molesta. Es la respuesta industrial
   para ambientes ruidosos. ~10 USD.
3. **Extensor de I2C diferencial** (PCA9615 o P82B96) sobre par trenzado.

### Actuadores — reemplazar los relés desnudos

- **Placa de relés opto-aislada con JD-VCC separado.** Casi todas las placas
  chinas traen un jumper JD-VCC: **sacalo** y alimentá la bobina con una fuente
  aparte. Así la única conexión entre la bobina y el MCU es el optoacoplador.
  Esto solo elimina los picos de alimentación.
- **Snubber RC en cada carga inductiva**: 100 Ω 0.5 W en serie con 0.1 µF
  X2 (275 VAC), en paralelo con los contactos. Más un MOV. Va **en la carga**, no
  en la placa de relés.
- **Fuentes separadas**: una para lógica (Raspberry + ESP32), otra para bobinas.
  Tierra en estrella, un único punto de unión.
- **SSR con cruce por cero** sirve para cargas resistivas, pero **no** para
  compresores: tiene corriente de fuga y falla en cerrado. Para el
  deshumidificador, relé de calidad o contactor.
- **Mejor que prender y apagar**: ventiladores EC con control 0–10 V o PWM. La
  velocidad variable estabiliza el VPD mucho mejor que el on/off, y no hay
  conmutación de red que genere ruido.

### Cableado y EMC — acá está la mayor parte del problema

- Par trenzado **blindado** para cualquier tirada de sensor de más de 30 cm.
  Malla a tierra **en un solo extremo**.
- Separá físicamente el cableado de red del de señal. Si se tienen que cruzar,
  que sea a 90°.
- Ferrita en el cable del sensor, del lado del MCU.
- Pull-ups de I2C de 2.2k–4.7k (los 10k por defecto son muy flojos con
  capacidad de cable).

### La Raspberry Pi

- **Booteá desde SSD por USB, no desde microSD.** Las SD se corrompen con
  escrituras constantes; es la falla más común en estos proyectos.
- **Activá el watchdog de hardware**: `dtparam=watchdog=on` en
  `/boot/firmware/config.txt` y `RuntimeWatchdogSec=15` en
  `/etc/systemd/system.conf`.
- **Corré los servicios con systemd**, no con `start_services.sh` + `pkill`.
  `Restart=always` hace bien y sin bugs lo que el script intenta hacer. Ver la
  sección siguiente.
- **Módulo RTC DS3231.** Sin él, la Pi arranca con la hora mal hasta que agarra
  NTP, y los timestamps del histórico salen torcidos.
- UPS o PoE HAT, o al menos asumir cortes sucios.

### Failsafe de hardware

Ningún failsafe de software sobrevive un kernel panic. Poné un backstop tonto:

- Un **humidistato mecánico** en serie con el humidificador, seteado al límite
  que nunca querés cruzar.
- Un **termostato bimetálico** como corte de temperatura.

Cuestan poco y son lo único que sigue funcionando cuando todo lo demás falló.

---

## systemd en lugar de start_services.sh

`scripts/start_services.sh` quedó arreglado, pero systemd lo hace mejor:
reinicia solo, no duplica procesos, y maneja el orden de arranque.

`/etc/systemd/system/autocann-vpd.service`:

```ini
[Unit]
Description=Autocann VPD control loop
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=autocann
WorkingDirectory=/home/autocann/Autocann
Environment=PATH=/home/autocann/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/home/autocann/.local/bin/uv run python -u -m autocann.cli.vpd
Restart=always
RestartSec=5
# El loop apaga los relés al recibir SIGTERM; dale tiempo.
KillSignal=SIGTERM
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/autocann-web.service`:

```ini
[Unit]
Description=Autocann web dashboard
After=network-online.target docker.service

[Service]
Type=simple
User=autocann
WorkingDirectory=/home/autocann/Autocann
Environment=PATH=/home/autocann/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/home/autocann/.local/bin/uv run python -m autocann.cli.backend
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now autocann-vpd autocann-web
journalctl -u autocann-vpd -f
```

systemd garantiza **una sola instancia**, que es exactamente el bug que hacía que
los relés golpearan.

---

## Si querés cambiar lo mínimo posible

Ordenado por relación beneficio/esfuerzo:

1. **Cambiá los DHT22 por SHT41 por I2C**, con el sensor cerca de la placa. Una
   tarde de trabajo, y se va la mayor parte del ruido de lecturas.
2. **Sacá el jumper JD-VCC** de la placa de relés y alimentá las bobinas con una
   fuente aparte. Veinte minutos.
3. **Poné snubbers RC** en el deshumidificador y los ventiladores. Una hora.
4. **Pasá a systemd.** Media hora, y elimina la clase de bugs de procesos
   duplicados.
5. **Booteá desde SSD** y agregá el watchdog. Una tarde.

Con esos cinco puntos, sobre el código ya corregido, deberías tener un sistema
que no te moleste. El rediseño con el control en el ESP32 vale la pena cuando
quieras ampliar (más carpas, CO₂, riego), no necesariamente ahora.

---

## Lo que ya resuelve el software

Para no comprar hardware buscando arreglar algo que ya está arreglado:

- **Mediana de 5 muestras** sobre las lecturas del sensor: rechaza los picos
  aislados que produce un sensor ruidoso sin agregar el retardo de un promedio.
- **Banda muerta por etapa**: el control no actúa mientras el VPD está en rango.
- **Tiempos mínimos de encendido, apagado y de cambio de sentido**: pase lo que
  pase con las lecturas, un relé no puede conmutar más rápido que eso.
- **Failsafe por dato viejo**: si no hay lectura fresca, apaga todo.
- **Un solo dueño del GPIO**: el override manual pasa por Redis, no por un
  segundo proceso abriendo el mismo pin.

Eso cubre el ruido eléctrico *moderado*. Lo que no puede hacer el software es
arreglar un sensor que entrega basura o contactos que se pegan.
