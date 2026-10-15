/*
 * Autocann - nodo de sensores interior (ESP32)
 *
 * Lee dos DHT22 y postea cada uno por separado a /api/sensor/indoor con su
 * propio sensor_id. El que manda es el primario; el testigo no controla nada,
 * existe para que el detector de deriva pueda agarrar al primario informando
 * un numero plausible y equivocado.
 *
 * ADEMAS REPORTA SU PROPIO ESTADO a /api/node/log: contadores por sensor y las
 * ultimas lineas que imprimio. El puerto serie dice exactamente que esta mal
 * -una lectura fallida, un POST que volvio 400, el WiFi que se cayo- y nadie lo
 * puede ver, porque leerlo significa entrar a la carpa con una notebook y
 * desenchufar el nodo de su fuente para enchufarlo al USB, lo que lo reinicia y
 * borra justo el estado que se queria mirar. Asi que el nodo manda el log solo
 * y se lee desde el navegador, en Sistema.
 *
 * CABLEADO (por sensor, breakout de 3 pines marcado "+ out -"):
 *
 *     +    -> 3V3   del ESP32   <-- NUNCA 5 V
 *     out  -> GPIO  (32 y 33)
 *     -    -> GND
 *
 * El breakout trae el pull-up a VCC a bordo. Alimentandolo con 5 V la linea de
 * datos queda en reposo a 5 V, y los GPIO del ESP32 no son tolerantes a 5 V.
 * Verificar con el tester: entre "+" y "out" tiene que haber unos 10 k.
 *
 * GPIO 32 y 33 no son pines de strapping y no tienen glitch de arranque.
 * Evitar 0, 2, 5, 12, 15 (strapping), 1 y 3 (consola USB), 6-11 (flash) y
 * 34-39 (solo entrada).
 *
 * Libreria: "DHT sensor library" de Adafruit (+ "Adafruit Unified Sensor").
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <DHT.h>
#include <esp_system.h>
#include <stdarg.h>

// WIFI_SSID, WIFI_PASSWORD y API_URL viven aca. El archivo esta en .gitignore
// para que la contrasena de la red no termine en el repositorio; se arranca
// copiando secrets.h.example.
#include "secrets.h"

// --------------------------------------------------------------------------
// Configuracion
// --------------------------------------------------------------------------

// Se muestra en el panel de Sistema. Subilo cuando cambies este archivo: es la
// unica forma de saber, mirando la web, si el nodo tiene grabado lo ultimo.
#define FW_VERSION "2026.10.12"

// Con que nombre se identifica este nodo ante la Raspberry. Mismas reglas que
// un sensor_id: minusculas, digitos, '_' y '-', hasta 16 caracteres.
#define NODE_NAME "sensores"

// Los ids tienen que coincidir con AUTOCANN_INDOOR_PRIMARY,
// AUTOCANN_INDOOR_WITNESS y AUTOCANN_INDOOR_OBSERVERS del lado de la Raspberry.
//
// En esta carpa 'b' esta ENCIMA de la lampara a proposito, para saber la
// temperatura ahi arriba, asi que va como OBSERVADOR y no como testigo: su
// offset contra 'a' se mueve con el fotoperiodo, de modo que el detector de
// deriva mediria la luz y no la salud del sensor. La instalacion queda SIN
// deteccion de deriva hasta que haya un tercer DHT22 al lado de 'a'.
struct SensorSpec {
  const char *id;
  uint8_t pin;
};

static SensorSpec SENSORS[] = {
  { "a", 32 },   // primario: el que decide
  { "b", 33 },   // observador: se lee y se muestra, no cruza con nadie
};
static const size_t SENSOR_COUNT = sizeof(SENSORS) / sizeof(SENSORS[0]);

// El lazo de control descarta datos de mas de 60 s (ESP32_MAX_AGE_SECONDS), asi
// que 10 s deja margen de sobra para un par de ciclos perdidos sin que el
// failsafe se dispare.
static const unsigned long POST_INTERVAL_MS = 10000;

// El DHT22 no se puede leer mas rapido que cada 2 s. Los dos se leen uno
// despues del otro con esta pausa en el medio.
static const unsigned long DHT_SETTLE_MS = 2200;

static const unsigned long WIFI_RETRY_MS = 500;
static const int WIFI_MAX_RETRIES = 40;   // ~20 s

// Cada cuanto se reporta el estado. El backend da por mudo al nodo a los 100 s
// (NODE_MAX_AGE_SECONDS), asi que esto deja perder dos reportes seguidos antes
// de que la web diga "sin contacto".
static const unsigned long REPORT_INTERVAL_MS = 30000;

// Un latido con las ultimas lecturas, para que el log no quede en blanco
// cuando todo anda bien. Cada 5 minutos: el log guarda 300 lineas, asi que un
// dia entero de latidos sigue entrando junto con los eventos.
static const unsigned long HEARTBEAT_MS = 300000;

// --------------------------------------------------------------------------
// Log propio
// --------------------------------------------------------------------------
//
// Cola circular en RAM. Lo que se encola es lo que vale la pena conservar
// -fallas, recuperaciones, arranques, un latido cada tanto-, no la lectura de
// cada ciclo: esa se imprime al serie igual, pero encolarla llenaria la cola de
// numeros normales y tiraria afuera justo la linea que explica la falla.
//
// Si el reporte no se puede mandar, las lineas quedan encoladas y se van con el
// proximo. Por eso una caida de WiFi se puede leer despues: las lineas que la
// cuentan sobreviven a la caida que impidio mandarlas.

static const size_t LOG_CAPACITY = 24;
static const size_t LOG_TEXT_MAX = 120;

struct LogLine {
  unsigned long ms;
  const char *level;
  char text[LOG_TEXT_MAX];
};

static LogLine logBuffer[LOG_CAPACITY];
static size_t logHead = 0;      // la mas vieja sin mandar
static size_t logCount = 0;
static uint32_t logDropped = 0; // lineas que se perdieron por cola llena

static void nodeLogf(const char *level, const char *fmt, ...) {
  char text[LOG_TEXT_MAX];
  va_list args;
  va_start(args, fmt);
  vsnprintf(text, sizeof(text), fmt, args);
  va_end(args);

  Serial.println(text);

  if (logCount == LOG_CAPACITY) {
    // Se tira la mas vieja, no la nueva: si el nodo esta sin conexion hace
    // rato, lo ultimo que paso explica mejor el estado actual que lo primero.
    logHead = (logHead + 1) % LOG_CAPACITY;
    logCount--;
    logDropped++;
  }

  LogLine &line = logBuffer[(logHead + logCount) % LOG_CAPACITY];
  line.ms = millis();
  line.level = level;
  strncpy(line.text, text, LOG_TEXT_MAX - 1);
  line.text[LOG_TEXT_MAX - 1] = '\0';
  logCount++;
}

/* Escapa lo que va adentro de un string JSON. */
static void appendJsonString(String &out, const char *text) {
  out += '"';
  for (const char *p = text; *p; p++) {
    char c = *p;
    if (c == '"' || c == '\\') {
      out += '\\';
      out += c;
    } else if (c == '\n' || c == '\r' || c == '\t') {
      out += ' ';
    } else if ((unsigned char)c < 0x20) {
      // Un caracter de control crudo invalida el JSON entero y con el se
      // pierde el reporte completo, no solo esa linea.
      out += ' ';
    } else {
      out += c;
    }
  }
  out += '"';
}

// --------------------------------------------------------------------------
// Estado por sensor
// --------------------------------------------------------------------------

struct SensorStats {
  uint32_t readsOk;
  uint32_t readsFailed;
  uint32_t consecutiveFailed;
  uint32_t postsOk;
  uint32_t postsFailed;
  int lastStatus;          // ultimo codigo HTTP, 0 si nunca se posteo
  float lastTemperature;
  float lastHumidity;
  bool hasReading;
};

static DHT *dhts[SENSOR_COUNT];
static SensorStats stats[SENSOR_COUNT];

static uint32_t wifiReconnects = 0;
static bool wifiWasConnected = false;
static unsigned long lastReport = 0;
static unsigned long lastHeartbeat = 0;
static bool reportFailing = false;

static const char *resetReasonName() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON:  return "encendido";
    case ESP_RST_EXT:      return "reset externo";
    case ESP_RST_SW:       return "reinicio por software";
    case ESP_RST_PANIC:    return "panic";
    case ESP_RST_INT_WDT:  return "watchdog de interrupcion";
    case ESP_RST_TASK_WDT: return "watchdog de tarea";
    case ESP_RST_WDT:      return "watchdog";
    case ESP_RST_BROWNOUT: return "brownout (cayo la alimentacion)";
    case ESP_RST_SDIO:     return "sdio";
    default:               return "desconocido";
  }
}

// --------------------------------------------------------------------------
// Red
// --------------------------------------------------------------------------

/*
 * La base del backend, sacada de API_URL.
 *
 * Asi secrets.h sigue teniendo un solo valor que tocar y los nodos ya grabados
 * no necesitan un secrets.h nuevo para empezar a reportar.
 */
static String apiBase() {
  String url(API_URL);
  int at = url.indexOf("/api/");
  if (at > 0) {
    url.remove(at);
  }
  return url;
}

static void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    wifiWasConnected = true;
    return;
  }

  if (wifiWasConnected) {
    wifiReconnects++;
    nodeLogf("warn", "WiFi: se cayo la conexion, reconectando");
    wifiWasConnected = false;
  }

  Serial.printf("WiFi: conectando a %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  for (int i = 0; i < WIFI_MAX_RETRIES && WiFi.status() != WL_CONNECTED; i++) {
    delay(WIFI_RETRY_MS);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    wifiWasConnected = true;
    nodeLogf("info", "WiFi conectado, ip %s, senal %d dBm",
             WiFi.localIP().toString().c_str(), WiFi.RSSI());
  } else {
    // No se reinicia ni se bloquea: el failsafe por dato viejo de la Raspberry
    // ya cubre el silencio, y el proximo ciclo vuelve a intentar.
    nodeLogf("error", "WiFi: sin conexion, reintento en el proximo ciclo");
  }
}

/* Postea una lectura. Devuelve true si el backend la acepto. */
static bool postReading(size_t index, float temperature, float humidity) {
  SensorStats &s = stats[index];
  const char *sensorId = SENSORS[index].id;

  if (WiFi.status() != WL_CONNECTED) {
    s.postsFailed++;
    return false;
  }

  char payload[128];
  snprintf(payload, sizeof(payload),
           "{\"temperature\":%.2f,\"humidity\":%.2f,\"sensor_id\":\"%s\"}",
           temperature, humidity, sensorId);

  HTTPClient http;
  http.setConnectTimeout(3000);
  http.setTimeout(3000);
  http.begin(API_URL);
  http.addHeader("Content-Type", "application/json");

  int status = http.POST(payload);
  bool wasFailing = s.lastStatus != 0 && s.lastStatus != 200;
  s.lastStatus = status;

  if (status != 200) {
    s.postsFailed++;
    // El cuerpo trae el motivo cuando el backend rechaza el dato (un id que no
    // existe, un rango imposible), que es lo que hace falta para arreglarlo.
    String body = status > 0 ? http.getString() : String();
    body.replace("\n", " ");
    nodeLogf("error", "sensor %s: el POST volvio %d %s", sensorId, status,
             status > 0 ? body.substring(0, 60).c_str()
                        : http.errorToString(status).c_str());
  } else {
    s.postsOk++;
    if (wasFailing) {
      nodeLogf("info", "sensor %s: el backend vuelve a aceptar las lecturas", sensorId);
    }
  }

  http.end();
  return status == 200;
}

/*
 * Manda el reporte propio. Las lineas se descartan solo si el backend las tomo.
 *
 * El reporte es lo que hace visible al nodo, asi que su propia falla tambien
 * tiene que quedar en el log; pero logueada una sola vez por racha, o el nodo
 * se llenaria la cola contandose a si mismo que no puede vaciar la cola.
 */
static void sendReport() {
  if (WiFi.status() != WL_CONNECTED) return;

  String body;
  body.reserve(512 + logCount * (LOG_TEXT_MAX + 48));
  body += "{\"node\":\"" NODE_NAME "\",\"firmware\":\"" FW_VERSION "\"";
  body += ",\"uptime_ms\":" + String(millis());
  body += ",\"ip\":\"" + WiFi.localIP().toString() + "\"";
  body += ",\"rssi\":" + String(WiFi.RSSI());
  body += ",\"free_heap\":" + String((uint32_t)ESP.getFreeHeap());
  body += ",\"wifi_reconnects\":" + String(wifiReconnects);
  body += ",\"boot_reason\":";
  appendJsonString(body, resetReasonName());

  body += ",\"sensors\":[";
  for (size_t i = 0; i < SENSOR_COUNT; i++) {
    if (i) body += ',';
    body += "{\"id\":\"" + String(SENSORS[i].id) + "\"";
    body += ",\"pin\":" + String(SENSORS[i].pin);
    body += ",\"reads_ok\":" + String(stats[i].readsOk);
    body += ",\"reads_failed\":" + String(stats[i].readsFailed);
    body += ",\"consecutive_failed\":" + String(stats[i].consecutiveFailed);
    body += ",\"posts_ok\":" + String(stats[i].postsOk);
    body += ",\"posts_failed\":" + String(stats[i].postsFailed);
    body += ",\"last_status\":" + String(stats[i].lastStatus);
    body += '}';
  }
  body += "],\"lines\":[";

  size_t sending = logCount;
  for (size_t i = 0; i < sending; i++) {
    const LogLine &line = logBuffer[(logHead + i) % LOG_CAPACITY];
    if (i) body += ',';
    body += "{\"ms\":" + String(line.ms) + ",\"level\":\"" + line.level + "\",\"text\":";
    appendJsonString(body, line.text);
    body += '}';
  }
  body += "]}";

  HTTPClient http;
  http.setConnectTimeout(3000);
  http.setTimeout(3000);
  http.begin(apiBase() + "/api/node/log");
  http.addHeader("Content-Type", "application/json");
  int status = http.POST(body);
  http.end();

  if (status == 200) {
    // Solo las que se mandaron: lo que se encolo mientras tanto se va en el
    // proximo reporte en vez de perderse.
    logHead = (logHead + sending) % LOG_CAPACITY;
    logCount -= sending;
    if (logDropped > 0) {
      uint32_t dropped = logDropped;
      logDropped = 0;
      nodeLogf("warn", "se perdieron %lu lineas de log mientras no habia contacto",
               (unsigned long)dropped);
    }
    if (reportFailing) {
      reportFailing = false;
      nodeLogf("info", "el reporte de estado vuelve a llegar");
    }
  } else if (!reportFailing) {
    reportFailing = true;
    nodeLogf("warn", "no se pudo mandar el reporte de estado (HTTP %d)", status);
  }
}

// --------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println("\nAutocann - nodo de sensores " FW_VERSION);

  for (size_t i = 0; i < SENSOR_COUNT; i++) {
    dhts[i] = new DHT(SENSORS[i].pin, DHT22);
    dhts[i]->begin();
    stats[i] = SensorStats();
    stats[i].lastStatus = 0;
    stats[i].hasReading = false;
    Serial.printf("sensor %s en GPIO %u\n", SENSORS[i].id, SENSORS[i].pin);
  }

  // Primera linea del log de cada arranque. Un nodo que se reinicia solo cada
  // tantas horas no deja ningun otro rastro, y el motivo distingue un corte de
  // luz de un brownout de la fuente, que se arreglan de maneras distintas.
  nodeLogf("info", "arranque " FW_VERSION ", motivo: %s", resetReasonName());

  connectWifi();
  lastReport = millis();
  lastHeartbeat = millis();
}

void loop() {
  connectWifi();

  for (size_t i = 0; i < SENSOR_COUNT; i++) {
    SensorStats &s = stats[i];
    float humidity = dhts[i]->readHumidity();
    float temperature = dhts[i]->readTemperature();

    if (isnan(humidity) || isnan(temperature)) {
      // Una lectura fallida se saltea en silencio hacia la Raspberry. Postear
      // un valor inventado seria peor que no postear nada: el lazo tiene
      // failsafe por dato viejo, pero ninguno por dato falso. Lo que no se
      // saltea es contarla: una falla cada tanto es normal en un DHT22, y una
      // racha es un cable flojo.
      s.readsFailed++;
      s.consecutiveFailed++;
      Serial.printf("sensor %s: lectura fallida\n", SENSORS[i].id);
      if (s.consecutiveFailed == 3) {
        nodeLogf("error", "sensor %s (GPIO %u): 3 lecturas fallidas seguidas",
                 SENSORS[i].id, SENSORS[i].pin);
      }
    } else {
      if (s.consecutiveFailed >= 3) {
        nodeLogf("info", "sensor %s: volvio a leer despues de %lu fallas",
                 SENSORS[i].id, (unsigned long)s.consecutiveFailed);
      }
      s.readsOk++;
      s.consecutiveFailed = 0;
      s.lastTemperature = temperature;
      s.lastHumidity = humidity;
      s.hasReading = true;
      Serial.printf("sensor %s: %.1f C, %.1f %%\n", SENSORS[i].id, temperature, humidity);
      postReading(i, temperature, humidity);
    }

    if (i + 1 < SENSOR_COUNT) {
      delay(DHT_SETTLE_MS);
    }
  }

  if (millis() - lastHeartbeat >= HEARTBEAT_MS) {
    lastHeartbeat = millis();
    String beat;
    for (size_t i = 0; i < SENSOR_COUNT; i++) {
      if (i) beat += " · ";
      beat += String(SENSORS[i].id) + ": ";
      beat += stats[i].hasReading
              ? String(stats[i].lastTemperature, 1) + " C " +
                String(stats[i].lastHumidity, 1) + " %"
              : String("sin lectura");
    }
    nodeLogf("info", "%s (senal %d dBm)", beat.c_str(), WiFi.RSSI());
  }

  if (millis() - lastReport >= REPORT_INTERVAL_MS) {
    lastReport = millis();
    sendReport();
  }

  delay(POST_INTERVAL_MS);
}
