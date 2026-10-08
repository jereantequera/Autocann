/*
 * Autocann - nodo de sensores interior (ESP32)
 *
 * Lee dos DHT22 y postea cada uno por separado a /api/sensor/indoor con su
 * propio sensor_id. El que manda es el primario; el testigo no controla nada,
 * existe para que el detector de deriva pueda agarrar al primario informando
 * un numero plausible y equivocado.
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

// WIFI_SSID, WIFI_PASSWORD y API_URL viven aca. El archivo esta en .gitignore
// para que la contrasena de la red no termine en el repositorio; se arranca
// copiando secrets.h.example.
#include "secrets.h"

// --------------------------------------------------------------------------
// Configuracion
// --------------------------------------------------------------------------

// Los ids tienen que coincidir con AUTOCANN_INDOOR_PRIMARY y
// AUTOCANN_INDOOR_WITNESS del lado de la Raspberry.
struct SensorSpec {
  const char *id;
  uint8_t pin;
};

static SensorSpec SENSORS[] = {
  { "a", 32 },   // primario: el que decide
  { "b", 33 },   // testigo: solo vigila al primario
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

// --------------------------------------------------------------------------

static DHT *dhts[SENSOR_COUNT];

static void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  Serial.printf("WiFi: conectando a %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  for (int i = 0; i < WIFI_MAX_RETRIES && WiFi.status() != WL_CONNECTED; i++) {
    delay(WIFI_RETRY_MS);
    Serial.print(".");
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf(" ok, ip %s\n", WiFi.localIP().toString().c_str());
  } else {
    // No se reinicia ni se bloquea: el failsafe por dato viejo de la Raspberry
    // ya cubre el silencio, y el proximo ciclo vuelve a intentar.
    Serial.println(" sin conexion, reintento en el proximo ciclo");
  }
}

/* Postea una lectura. Devuelve true si el backend la acepto. */
static bool postReading(const char *sensorId, float temperature, float humidity) {
  if (WiFi.status() != WL_CONNECTED) {
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
  if (status != 200) {
    Serial.printf("sensor %s: POST devolvio %d (%s)\n",
                  sensorId, status, http.errorToString(status).c_str());
  }
  http.end();

  return status == 200;
}

void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println("\nAutocann - nodo de sensores");

  for (size_t i = 0; i < SENSOR_COUNT; i++) {
    dhts[i] = new DHT(SENSORS[i].pin, DHT22);
    dhts[i]->begin();
    Serial.printf("sensor %s en GPIO %u\n", SENSORS[i].id, SENSORS[i].pin);
  }

  connectWifi();
}

void loop() {
  connectWifi();

  for (size_t i = 0; i < SENSOR_COUNT; i++) {
    float humidity = dhts[i]->readHumidity();
    float temperature = dhts[i]->readTemperature();

    if (isnan(humidity) || isnan(temperature)) {
      // Una lectura fallida se saltea en silencio. Postear un valor inventado
      // seria peor que no postear nada: el lazo tiene failsafe por dato viejo,
      // pero ninguno por dato falso.
      Serial.printf("sensor %s: lectura fallida\n", SENSORS[i].id);
    } else {
      Serial.printf("sensor %s: %.1f C, %.1f %%\n", SENSORS[i].id, temperature, humidity);
      postReading(SENSORS[i].id, temperature, humidity);
    }

    if (i + 1 < SENSOR_COUNT) {
      delay(DHT_SETTLE_MS);
    }
  }

  delay(POST_INTERVAL_MS);
}
