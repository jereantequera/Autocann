# La plataforma

Autocann pasa de ser una carpa a ser un producto: equipos instalados en casa de
los clientes, para cualquier cultivo, que mandan sus datos a nuestra nube. Este
documento explica el diseño y el porqué de cada decisión.

## Principios

1. **El control se queda en el equipo.** La nube recibe datos y manda
   parámetros; la decisión de prender o apagar se toma en el equipo. Si se cae
   internet o la nube, las plantas no se enteran. Y si alguien compromete la
   nube, no maneja miles de carpas.
2. **Cada equipo tiene una identidad propia guardada en hardware.** Una clave
   privada que no se puede extraer (chip seguro ATECC608 o el periférico DS del
   ESP32-S3) y un certificado firmado por nuestra CA. Nada de claves
   compartidas: si se filtra una, se revoca un equipo, no todos.
3. **mTLS siempre, también en desarrollo.** No hay modo sin TLS. Un modo
   inseguro que existe termina prendido en producción.
4. **Los cultivos son datos.** Etapas, duraciones y rangos son un perfil que la
   nube manda y el equipo valida y guarda, no constantes en el código.
5. **Estándares antes que inventos.** mTLS, X.509 y MQTT, en lugar de un
   esquema propio de firmas: es lo que un auditor o un cliente grande va a
   querer ver, y tiene herramientas para revocar, rotar y auditar.

## Cómo se conecta un equipo

```
[Equipo]                                        [Nube]
 ESP32-S3 + clave en hardware ──mTLS/MQTT──▶ Broker ──▶ autocann-ingest ──▶ base de series de tiempo
   control local                 :8883       (ACL por CN)    (valida)
   perfil guardado          ◀── comandos acotados, perfiles, OTA firmado ──
```

### Tópicos

```
v1/devices/{device_id}/telemetry   equipo → nube    lecturas
v1/devices/{device_id}/state       equipo → nube    salidas, perfil activo, firmware
v1/devices/{device_id}/commands    nube → equipo    perfiles, órdenes acotadas
```

Definidos en `crates/protocol/src/topics.rs`.

### Identidad y permisos

- El **CN del certificado** de un equipo es su `device_id`. El broker lo usa
  como nombre de usuario.
- La **ACL** deja que cada equipo publique y escuche sólo bajo su propio id. Esa
  regla es lo que vuelve confiable el `device_id` del tópico: el ingestor no lo
  vuelve a verificar.
- Los **servicios** (el ingestor) tienen CN `svc:<nombre>`. El `:` no es válido
  en un `device_id`, así que ningún equipo puede llamarse igual que un servicio
  y heredar sus permisos.

### Los mensajes

```json
{"v":1,"seq":42,"readings":[{"sensor":"a","temperature_c":24.5,"humidity_pct":62.0}]}
```

- Sin hora: el equipo no tiene reloj confiable; la hora la pone quien recibe.
- `seq` sube con cada mensaje y vuelve a cero al reiniciar: muestra huecos y
  reinicios.
- Rangos físicos iguales a los de la carpa. Un mensaje con una lectura mala se
  rechaza entero.

## El broker: Mosquitto para desarrollo, EMQX para producción

`deploy/mosquitto/` alcanza para probar en una mesa, pero en las pruebas de
punta a punta aparecieron dos cosas que Mosquitto no puede impedir:

1. **Un equipo puede tomar el client id de otro.** La ACL le impide publicar en
   nombre del otro, pero conectarse con su client id desconecta al legítimo.
   EMQX puede forzar `client id == CN`.
2. **Un equipo puede meterse en una suscripción compartida y robar mensajes.**
   Mosquitto acepta la suscripción `$share/...` de cualquiera y le reparte
   mensajes que después la ACL descarta. Probado: con un intruso en el grupo,
   el ingestor real recibió la mitad. Por eso el ingestor usa una suscripción
   común por defecto, y la compartida (`AUTOCANN_INGEST_SHARE_GROUP`) queda
   para un broker que niegue la suscripción por ACL.

## La CA

- **Desarrollo** (`deploy/pki/dev-pki.sh`): raíz, intermedia y certificados en
  `deploy/pki/out/`, todo en una máquina. Sirve para una mesa.
- **Producción:** la raíz se genera y vive offline, y sólo firma intermedias.
  La clave de cada equipo nace dentro del equipo (o viene en su chip seguro).
  Lo que se firma es un CSR, y la clave privada nunca sale del hardware. Hace
  falta revocación (CRL u OCSP) desde el primer equipo vendido.

## Fases

| Fase | Qué | Hardware |
|---|---|---|
| **0** ← acá | Protocolo, ingestor, simulador, CA de desarrollo, broker con mTLS y ACL | Ninguno |
| **1** | Firmware del equipo: control local, MQTT con mTLS, perfil guardado | ESP32-S3 DevKit |
| **2** | Vinculación del equipo con su dueño por QR, OTA firmado, Secure Boot y flash encryption, revocación | ESP32-S3 DevKit |
| **3** | Hardware final (chip seguro o DS), carga de claves en fábrica, nube en producción | Placa propia o comercial |

## Decisiones abiertas

- **Hardware del equipo:** ATECC608 (viene con certificado de fábrica) o el
  periférico DS del ESP32-S3.
- **Dónde se guarda la telemetría:** TimescaleDB o Postgres. Entra en
  `crates/ingest` sin tocar la validación.
- **Construir o comprar el broker:** EMQX propio o AWS IoT Core.
- **Datos personales:** cliente y ubicación son datos personales (Ley 25.326).
  Guardar lo mínimo y cifrar en reposo.
