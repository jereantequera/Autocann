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
 */

#include <WiFi.h>
#include <HTTPClient.h>

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

// --------------------------------------------------------------------------

static bool applied[OUTPUT_COUNT];
static unsigned long lastGoodPoll = 0;
static unsigned long lastHeartbeat = 0;
static bool failsafeActive = true;   // hasta que la Pi diga otra cosa
//: Se loguea el vencimiento del watchdog una sola vez. Sin esto, cuando
//: vence sin nada encendido no deja rastro, que es justo el caso en que
//: mas querrias saber cuando perdio contacto.
static bool watchdogExpired = false;

static void writeOutput(size_t i, bool on) {
  digitalWrite(OUTPUTS[i].pin, on ? ON_LEVEL : OFF_LEVEL);
  applied[i] = on;
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

/* Una consulta. Devuelve true si se aplico un estado valido. */
static bool poll() {
  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;
  http.setConnectTimeout(3000);
  http.setTimeout(3000);
  http.begin(API_URL);
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
