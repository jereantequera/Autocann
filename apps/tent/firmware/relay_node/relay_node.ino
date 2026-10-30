/*
 * Autocann - nodo de reles (ESP32)
 *
 * Consulta a la Raspberry cual es el estado deseado de las salidas y lo
 * aplica. La Pi decide; este nodo ejecuta y se cuida solo.
 *
 * POR QUE CONSULTA EN VEZ DE RECIBIR
 *
 * La Pi podria postearle a este nodo, pero entonces tendria que conocer su IP,
 * y con DHCP eso se rompe solo. Consultando, el nodo usa la misma direccion
 * fija que ya usa el de sensores.
 *
 * Y mas importante: la consulta ES el latido. Una consulta fallida es un
 * latido perdido, asi que un solo mecanismo cubre WiFi caida, Pi colgada,
 * backend muerto y cable desenchufado. Sin temporizadores separados que puedan
 * desincronizarse entre si.
 *
 * CABLEADO (placa de 4 canales opto, activo-bajo)
 *
 *     GND  -> GND del ESP32, y negativo de la fuente de 5 V (cable corto y grueso)
 *     IN1  -> GPIO 25   humidity_up
 *     IN2  -> GPIO 26   humidity_down
 *     IN3  -> GPIO 27   ventilation
 *     IN4  -> libre
 *     VCC  -> 3V3 del ESP32    <-- NUNCA 5 V: los pines IN reposarian en 5 V
 *
 *     Header de 2 pines, con el jumper SACADO:
 *     JD-VCC -> +5 V de una fuente aparte
 *     VCC    -> nada (misma red que el VCC del header de senal)
 *
 * Las cargas van siempre en COM + NO, nunca en NC: desenergizado tiene que
 * ser apagado.
 *
 *     LED IR -> GPIO 19, con 100 ohm en serie, pegado a la ventanita del
 *               receptor del humidificador. Si tiene que cruzar la carpa,
 *               NPN con 47 ohm desde los 5 V y 1 k en la base.
 *
 * POR QUE HAY UN LED INFRARROJO
 *
 * Darle energia al humidificador no lo prende: arranca en standby y espera que
 * le aprieten el boton. O sea que el rele lo HABILITA, no lo enciende, y sin
 * algo que mande el ON la salida queda encendida sin que salga niebla.
 *
 * El pulso nace aca y no en la Pi. `/api/outputs/desired` son booleanos de
 * estado deseado, y un estado deseado no puede decir "apreta ON ahora": el
 * pulso es un evento y vive en la transicion apagado->encendido, que es algo
 * que solo conoce el que la aplica. Ademas asi el endpoint, el watchdog y el
 * failsafe quedan igual que antes.
 *
 * EL CONTROL ES UN TOGGLE, Y ESO MANDA SOBRE TODO EL DISENO
 *
 * El control remoto tiene un solo boton de encendido, verificado el 2026-10-29:
 * manda siempre NEC 0x01FE48B7 y el humidificador es el que alterna. Entonces
 * DOS PULSOS SEGUIDOS LO APAGAN, y reintentar "por las dudas" es peor que no
 * hacer nada.
 *
 * Lo que vuelve manejable el toggle es que el rele define el estado: cada vez
 * que humidity_up cierra, el equipo arranca en standby, que es conocido, y
 * desde ahi un solo pulso lo prende. Por eso el pulso se manda SOLO en la
 * transicion, nunca repetido y nunca con la salida ya encendida de antes.
 *
 * Si el pulso se pierde, el ciclo pasa sin niebla. Es el lado seguro de la
 * falla: nunca deja encendido algo que deberia estar apagado, y cortar el rele
 * lo apaga igual pase lo que pase. Para volver a intentarlo no hay que mandar
 * otro pulso a ciegas sino apagar y prender humidity_up — el override manual
 * del panel alcanza —, porque eso devuelve el equipo a standby y recien ahi el
 * pulso vuelve a tener un estado conocido debajo.
 *
 * Lo que esto NO cubre: si el equipo se apaga solo con el rele ya cerrado (por
 * ejemplo, tanque vacio), no hay transicion nueva y nadie se entera. Eso pide
 * realimentacion, y como este humidificador no tiene luz indicadora, el camino
 * es un sensor de corriente en el cable.
 *
 * El resto del control, por si alguna vez sirve (todos NEC de 32 bits):
 *
 *     ON / POWER   0x01FE48B7      PURIFY       0x01FE609F
 *     FOG METER    0x01FE7887      TIMING       0x01FE50AF
 *     CONSTANT     0x01FE20DF      SCREEN OFF   0x01FEF807
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <IRsend.h>

#include "secrets.h"

// --------------------------------------------------------------------------
// Configuracion
// --------------------------------------------------------------------------

struct Output {
  const char *name;   // tiene que coincidir con autocann.hardware.outputs
  uint8_t pin;
};

static Output OUTPUTS[] = {
  { "humidity_up",   25 },
  { "humidity_down", 26 },
  { "ventilation",   27 },
};
static const size_t OUTPUT_COUNT = sizeof(OUTPUTS) / sizeof(OUTPUTS[0]);

//: La placa es activa-baja: LOW energiza el rele.
static const int ON_LEVEL  = LOW;
static const int OFF_LEVEL = HIGH;

static const unsigned long POLL_INTERVAL_MS = 2000;

/*
 * Sin una consulta exitosa durante este tiempo, se desenergiza todo.
 *
 * Tiene que ser bastante mas largo que el intervalo de consulta para que un
 * hipo de WiFi no haga golpear los reles, y bastante mas corto que cualquier
 * plazo en el que una salida pegada haga dano. Treinta consultas perdidas.
 */
static const unsigned long WATCHDOG_MS = 60000;

static const unsigned long HEARTBEAT_MS = 30000;
static const int WIFI_MAX_RETRIES = 40;   // ~20 s

/*
 * GPIO 19: libre, no es de strapping y no tiene glitch de arranque.
 *
 * Y el numero no se repite en ningun otro lado de la instalacion, que no es un
 * detalle menor aca: la Raspberry usa 4, 7, 13, 16 y 25, y ese 25 ya coincide
 * con el humidity_up de este nodo — una colision que mandaba a medir el header
 * equivocado y costo escribir pin_sources() para desarmarla. Poner el LED en
 * 13 habria creado la misma trampa contra el DHT22 exterior.
 *
 * Que al arrancar el pin quede flotando un instante no importa: en ese momento
 * todos los reles estan desenergizados, asi que el humidificador no tiene
 * corriente y no hay nada que pueda recibir un pulso espurio.
 */
static const uint16_t IR_LED_PIN = 19;

//: El boton de encendido del control. Capturado el 2026-10-29 con un receptor
//: VS1838B; los bytes 01 FE 48 B7 se complementan de a pares, que es lo que
//: distingue una trama NEC real de ruido que decodifico de casualidad.
static const uint64_t IR_POWER_CODE = 0x01FE48B7;

/*
 * Cuanto esperar entre cerrar el rele y mandar el pulso.
 *
 * El humidificador tiene que terminar de arrancar antes de poder escuchar el
 * infrarrojo. Tres segundos es una estimacion conservadora y esta para ajustar
 * con el equipo en la mano: llegar tarde no cuesta nada, llegar temprano
 * significa que el pulso se pierde y el ciclo pasa sin niebla.
 *
 * La granularidad real es la del lazo, asi que en la practica sale entre
 * IR_BOOT_DELAY_MS y un POLL_INTERVAL_MS mas tarde. Da igual: lo unico que
 * importa es no adelantarse.
 */
static const unsigned long IR_BOOT_DELAY_MS = 3000;

IRsend irsend(IR_LED_PIN);

// --------------------------------------------------------------------------

static bool applied[OUTPUT_COUNT];
static unsigned long lastGoodPoll = 0;
static unsigned long lastHeartbeat = 0;
static bool failsafeActive = true;   // hasta que la Pi diga otra cosa
//: Se loguea el vencimiento del watchdog una sola vez. Sin esto, cuando
//: vence sin nada encendido no deja rastro, que es justo el caso en que
//: mas querrias saber cuando perdio contacto.
static bool watchdogExpired = false;

/*
 * El control remoto virtual del panel (autocann/hardware/ir_remote.py).
 *
 * Un boton apretado en la web viaja en la misma consulta, como
 * "ir":{"id":N,"code":"0x..."}. El nodo lo manda una sola vez y devuelve el id
 * en cada consulta siguiente (?ir_ack=N), que es lo que lo borra en la Pi. Si
 * el id es el mismo que el ultimo mandado, no se repite: POWER es un toggle y
 * dos pulsos se anulan.
 *
 * Se arranca en 0, que la Pi nunca asigna (su contador empieza en 1). Despues
 * de un reinicio el nodo no se acuerda del ultimo id, pero tampoco importa: los
 * reles arrancan apagados, asi que el equipo no tiene energia para un pulso
 * repetido, y la orden vence sola a los pocos segundos.
 */
static uint32_t lastIrId = 0;

/*
 * Cual de las salidas alimenta el humidificador.
 *
 * Se resuelve por nombre en setup() en vez de escribir el indice a mano: el
 * arreglo OUTPUTS es la autoridad del cableado y puede reordenarse sin que
 * nadie se acuerde de que ademas hay un pulso colgado de una de sus filas.
 * OUTPUT_COUNT significa "no esta", y entonces no se manda ningun pulso.
 */
static size_t humidifierIndex = OUTPUT_COUNT;

//: Pulso armado y esperando que el equipo termine de arrancar.
static bool pulsePending = false;
static unsigned long pulseDueAt = 0;

static void writeOutput(size_t i, bool on) {
  bool was = applied[i];
  digitalWrite(OUTPUTS[i].pin, on ? ON_LEVEL : OFF_LEVEL);
  applied[i] = on;

  if (i != humidifierIndex || on == was) return;

  if (on) {
    // Recien energizado: el equipo esta en standby, que es el unico estado
    // desde el cual un pulso suelto lo prende con certeza.
    pulseDueAt = millis() + IR_BOOT_DELAY_MS;
    pulsePending = true;
  } else {
    // Se corto antes de llegar a mandarlo. Mandarlo igual seria hablarle a un
    // equipo sin corriente, y dejarlo armado haria que saliera tarde, ya sobre
    // el proximo encendido y contra un estado que no es el que se asumio.
    pulsePending = false;
  }
}

/*
 * Manda el pulso si ya se cumplio la espera.
 *
 * La resta antes de comparar es para que el vencimiento siga siendo correcto
 * cuando millis() da la vuelta a los 49 dias.
 */
static void servicePulse() {
  if (!pulsePending) return;
  if ((long)(millis() - pulseDueAt) < 0) return;

  pulsePending = false;
  irsend.sendNEC(IR_POWER_CODE);
  Serial.println("IR: pulso POWER enviado al humidificador");
}

static void allOff(const char *reason) {
  bool changed = false;
  for (size_t i = 0; i < OUTPUT_COUNT; i++) {
    if (applied[i]) changed = true;
    writeOutput(i, false);
  }
  if (changed || !failsafeActive) {
    Serial.printf("!! FAILSAFE: todo apagado (%s)\n", reason);
  }
  failsafeActive = true;
}

static void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.printf("WiFi: conectando a %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  for (int i = 0; i < WIFI_MAX_RETRIES && WiFi.status() != WL_CONNECTED; i++) {
    delay(500);
    Serial.print(".");
  }
  Serial.println(WiFi.status() == WL_CONNECTED
                 ? " ok, ip " + WiFi.localIP().toString()
                 : String(" sin conexion"));
}

/*
 * Busca "nombre":true / "nombre":false. Devuelve 1, 0, o -1 si no aparece.
 *
 * Se le sacan los espacios al cuerpo antes de buscar, porque Flask serializa
 * compacto en produccion y con espacios en modo debug, y el nodo no se tiene
 * que enterar de esa diferencia.
 */
static int findBool(const String &body, const char *name) {
  String key = String("\"") + name + "\":";
  int at = body.indexOf(key);
  if (at < 0) return -1;
  int valueAt = at + key.length();
  if (body.startsWith("true", valueAt)) return 1;
  if (body.startsWith("false", valueAt)) return 0;
  return -1;
}

/*
 * Lee "ir":{"id":N,"code":"0x..."}. Devuelve false si no hay boton ("ir":null)
 * o si no se entiende, y en ese caso no se manda nada.
 */
static bool findIrCommand(const String &body, uint32_t &id, uint32_t &code) {
  int at = body.indexOf("\"ir\":{");
  if (at < 0) return false;
  int end = body.indexOf('}', at);
  if (end < 0) return false;
  String ir = body.substring(at, end);

  int idAt = ir.indexOf("\"id\":");
  int codeAt = ir.indexOf("\"code\":\"");
  if (idAt < 0 || codeAt < 0) return false;

  id = strtoul(ir.c_str() + idAt + 5, nullptr, 10);
  code = strtoul(ir.c_str() + codeAt + 8, nullptr, 16);
  return id != 0;
}

/*
 * Manda el boton del control remoto virtual, si hay uno nuevo.
 *
 * Se espera, sin confirmar, mientras el equipo esta arrancando (hay un pulso de
 * encendido pendiente) o sin energia: el pulso se perderia, y como no se
 * confirma la Pi lo vuelve a ofrecer en la consulta siguiente hasta que vence.
 */
static void serviceRemote(const String &body) {
  uint32_t id, code;
  if (!findIrCommand(body, id, code) || id == lastIrId) return;
  if (humidifierIndex >= OUTPUT_COUNT || !applied[humidifierIndex] || pulsePending) return;

  irsend.sendNEC(code);
  lastIrId = id;
  Serial.printf("IR: control remoto, boton 0x%08lX (orden %lu)\n",
                (unsigned long)code, (unsigned long)id);
}

/* Una consulta. Devuelve true si se aplico un estado valido. */
static bool poll() {
  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;
  http.setConnectTimeout(3000);
  http.setTimeout(3000);
  String url = String(API_URL) + (strchr(API_URL, '?') ? "&" : "?") + "ir_ack=" + lastIrId;
  http.begin(url);
  int status = http.GET();
  if (status != 200) {
    Serial.printf("consulta: HTTP %d\n", status);
    http.end();
    return false;
  }

  String body = http.getString();
  http.end();
  body.replace(" ", "");
  body.replace("\n", "");
  body.replace("\r", "");

  // Todas las salidas tienen que venir. Un cuerpo incompleto se rechaza
  // entero: aplicar la mitad de un estado es peor que no aplicar nada.
  bool wanted[OUTPUT_COUNT];
  for (size_t i = 0; i < OUTPUT_COUNT; i++) {
    int v = findBool(body, OUTPUTS[i].name);
    if (v < 0) {
      Serial.printf("respuesta sin '%s', se descarta entera\n", OUTPUTS[i].name);
      return false;
    }
    wanted[i] = (v == 1);
  }

  bool isFailsafe = findBool(body, "failsafe") == 1;
  bool changed = false;
  for (size_t i = 0; i < OUTPUT_COUNT; i++) {
    if (wanted[i] != applied[i]) {
      Serial.printf("%s -> %s\n", OUTPUTS[i].name, wanted[i] ? "ON" : "off");
      changed = true;
    }
    writeOutput(i, wanted[i]);
  }
  if (isFailsafe && !failsafeActive) {
    Serial.println("la Pi respondio en failsafe: todo apagado");
  }
  failsafeActive = isFailsafe;
  (void)changed;

  // Despues de aplicar las salidas: si esta misma consulta prendio el
  // humidificador, el pulso de arranque ya quedo armado y el boton espera.
  if (!isFailsafe) serviceRemote(body);
  return true;
}

void setup() {
  // El estado seguro se escribe ANTES de declarar el pin como salida: asi el
  // latch ya vale OFF cuando el pin pasa a salida, y no hay un pulso que haga
  // golpear los reles en cada arranque.
  for (size_t i = 0; i < OUTPUT_COUNT; i++) {
    digitalWrite(OUTPUTS[i].pin, OFF_LEVEL);
    pinMode(OUTPUTS[i].pin, OUTPUT);
    digitalWrite(OUTPUTS[i].pin, OFF_LEVEL);
    applied[i] = false;
  }

  Serial.begin(115200);
  delay(200);
  Serial.println("\nAutocann - nodo de reles");
  for (size_t i = 0; i < OUTPUT_COUNT; i++) {
    Serial.printf("  %-15s GPIO %u\n", OUTPUTS[i].name, OUTPUTS[i].pin);
  }
  Serial.printf("consulta cada %lu ms, watchdog a los %lu ms\n",
                POLL_INTERVAL_MS, WATCHDOG_MS);

  for (size_t i = 0; i < OUTPUT_COUNT; i++) {
    if (strcmp(OUTPUTS[i].name, "humidity_up") == 0) humidifierIndex = i;
  }
  irsend.begin();
  if (humidifierIndex < OUTPUT_COUNT) {
    Serial.printf("IR: LED en GPIO %u, POWER 0x%08lX a los %lu ms de energizar\n",
                  IR_LED_PIN, (unsigned long)IR_POWER_CODE, IR_BOOT_DELAY_MS);
  } else {
    // Sin esta salida no hay transicion de la cual colgar el pulso. El nodo
    // sigue sirviendo para todo lo demas, pero el humidificador se va a quedar
    // en standby y conviene que quede dicho en vez de descubrirse en la carpa.
    Serial.println("!! IR: no existe la salida 'humidity_up', no se enviara ningun pulso");
  }

  connectWifi();
  // Arranca el reloj ahora: si la Pi no contesta nunca, el watchdog tiene que
  // vencer igual en vez de quedarse esperando una primera consulta buena.
  lastGoodPoll = millis();
}

void loop() {
  connectWifi();

  if (poll()) {
    if (watchdogExpired) {
      Serial.println("** contacto recuperado, vuelve el control de la Pi");
      watchdogExpired = false;
    }
    lastGoodPoll = millis();
  } else if (millis() - lastGoodPoll > WATCHDOG_MS) {
    if (!watchdogExpired) {
      watchdogExpired = true;
      Serial.printf("** WATCHDOG VENCIDO a los %lu s sin contacto: desenergizo todo\n",
                    (millis() - lastGoodPoll) / 1000);
    }
    allOff("sin respuesta de la Pi");
  }

  // Despues de aplicar el estado, para que un encendido de esta misma vuelta
  // ya quede armado y el pulso no se atrase una consulta entera.
  servicePulse();

  if (millis() - lastHeartbeat > HEARTBEAT_MS) {
    lastHeartbeat = millis();
    Serial.printf("[%lus sin contacto] ", (millis() - lastGoodPoll) / 1000);
    for (size_t i = 0; i < OUTPUT_COUNT; i++) {
      Serial.printf("%s=%s ", OUTPUTS[i].name, applied[i] ? "ON" : "off");
    }
    Serial.println();
  }

  delay(POLL_INTERVAL_MS);
}
