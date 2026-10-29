# Control remoto del humidificador — códigos capturados

Capturado el 2026-10-29 con un NodeMCU ESP8266, receptor VS1838B en D5 (GPIO 14),
librería IRremoteESP8266 2.9.0.

Todo el control es **NEC de 32 bits**, cabecera 9000/4500 µs, con los dos primeros
bytes siempre `01 FE`. Las tramas están bien formadas: NEC manda cada byte seguido
de su complemento, y los cuatro pares cierran en los seis códigos. Eso es lo que
distingue una captura real de ruido que decodificó por casualidad.

## El mapa

| # | Botón | Código | Comando |
|---|---|---|---|
| 1 | ON (POWER) | `0x01FE48B7` | `0x48` |
| 2 | FOG METER | `0x01FE7887` | `0x78` |
| 3 | CONSTANT | `0x01FE20DF` | `0x20` |
| 4 | PURIFY | `0x01FE609F` | `0x60` |
| 5 | TIMING | `0x01FE50AF` | `0x50` |
| 6 | SCREEN OFF | `0x01FEF807` | `0xF8` |

El mapa está completo y confirmado. Los cuatro primeros salieron del orden
acordado; los dos últimos quedaron ambiguos y se desempataron apretando SCREEN
OFF una vez más, que repitió `0x01FEF807` — así que el otro es TIMING.

SCREEN OFF es el único que la librería da siempre como `UNKNOWN`, las tres veces
que se apretó y con el mismo hash. La trama llega entera igual, así que es una
particularidad de temporizado de ese botón y no afecta nada: cuando *enviemos*
vamos a generar NEC de manual.

## ON es un toggle, confirmado

`0x01FE48B7` salió idéntico en dos capturas separadas, con el equipo en estados
distintos. El control manda siempre el mismo código y el humidificador es el que
alterna. Entonces:

- **Dos envíos seguidos lo apagan.** Prohibido reintentar a ciegas.
- Lo que vuelve manejable el toggle es que el relé define el estado: cada cierre
  de `humidity_up` arranca el equipo en standby, que es conocido, y desde ahí un
  solo pulso lo prende.
- Un pulso perdido falla para el lado seguro: un ciclo sin niebla, nunca un equipo
  encendido de más.
- Para volver a prenderlo a mano, la forma segura **no** es mandar el POWER pelado
  —si estaba prendido, lo apaga— sino cortar el relé, esperar y volver a darle
  energía antes del pulso. Así el resultado no depende de adivinar el estado.

No tiene luz indicadora, así que la realimentación por LDR queda descartada. Lo
que cerraría el lazo es un sensor de corriente en el cable (ACS712 o SCT-013).

## Dos tramas salieron UNKNOWN y se recuperaron

De las seis, dos las dio la librería como `UNKNOWN` de 34 bits. No estaban rotas:
tenían `rawData[67]`, que es exactamente el largo de una NEC completa —cabecera,
32 bits y stop—, así que se recibieron enteras y lo que falló fue la tolerancia
del decodificador, no la captura.

`decodificar.py` las decodifica a mano y se calibra solo: las cinco tramas que la
librería sí reconoció tienen que dar el mismo código por ese camino. Dan. Con el
criterio de bits validado, los dos códigos recuperados son confiables.

De paso, las tres UNKNOWN de la primera sesión resultaron ser copias maltratadas
del mismo ON, no códigos distintos.

Si vuelven a aparecer muchos UNKNOWN, subir la tolerancia del decodificador; la
causa habitual es distancia, ángulo o pila floja.

## La prueba de lazo no sirve, no repetirla

Emitir y escuchar a la vez en el mismo ESP8266 **no funciona**: el receptor no
oye la propia transmisión de la placa. Demostrado con un LED desoldado de un
control remoto que andaba — o sea, un emisor seguro — que dio 270 envíos sin una
sola recepción, mientras el mismo receptor captaba el control remoto sin
problema. El lazo da negativo siempre, emita o no emita.

Para saber si un LED infrarrojo emite, la prueba válida es **mirarlo con la
cámara frontal del celular** mientras se lo excita: las traseras suelen traer un
filtro IR más fuerte y pueden no mostrarlo. Lo definitivo, igual, es probar
contra el humidificador.

## El LED negro del kit no sirve: es receptor

Probado el 2026-10-29 con el banco armado: 450 envíos por ese LED y cero tramas
recibidas; en cuanto se apretó el control remoto apuntando al mismo receptor
entraron 12 seguidas. O sea que el receptor oía perfecto y el LED no emitía.
Confirmado después mirándolo con la cámara frontal del celular mientras el
sketch transmitía: no destella.

Durante buena parte de la prueba el receptor había quedado desconectado, así que
los primeros 349 envíos en silencio no probaban nada. La conclusión vale por el
control posterior, no por esos.

Hace falta un emisor de verdad: desoldado de la punta de un control remoto viejo,
un módulo KY-005, o un LED de 5 mm de 940 nm.

## Para enviarlos después

```cpp
irsend.sendNEC(0x01FE48B7);   // 32 bits es el default de sendNEC
```
