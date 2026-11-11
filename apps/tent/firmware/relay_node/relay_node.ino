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
 * COMO SE RECUPERA SOLO, Y HASTA DONDE
 *
 * El 2026-11-10 a las 18:14 este nodo dejo de consultar y se quedo asi quince
 * horas, con la carpa sin control y nadie enterado. No se habia caido sin
 * poder levantarse: no estaba programado para levantarse. Toda su respuesta a
 * perder contacto era abrir los reles y seguir consultando, para siempre.
 *
 * Ahora hay tres escalones, de lo barato a lo caro:
 *
 *   60 s sin consulta buena  ->  se abren los reles (WATCHDOG_MS). La carpa
 *                                queda en un estado conocido y seguro.
 *   5 min sin NINGUN contacto -> ESP.restart() (REBOOT_AFTER_MS). Lo unico
 *                                que destraba la radio o la pila de red.
 *   90 s sin alimentar el loop -> lo reinicia el chip (TWDT_TIMEOUT_MS), que
 *                                es el unico que puede con un cuelgue real.
 *
 * El segundo escalon mira el ultimo contacto HTTP y no la ultima consulta
 * buena: si la Pi contesta cualquier cosa, el problema no es este nodo y
 * reiniciarse no arregla nada. Ver lastContact.
 *
 * Y hay un cuarto caso que ningun escalon arregla: si se cae la alimentacion,
 * no hay firmware que lo levante. Por eso el nodo ademas CUENTA de si mismo en
 * la consulta — por que arranco, cuanto hace, la senal y los reintentos de
 * WiFi — que es lo que deja distinguir un brownout de un cuelgue de un
 * problema de antena en vez de mirar las tres como "el nodo no esta".
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
#include <esp_task_wdt.h>
#include <esp_system.h>

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

/*
 * Sin NINGUN contacto con la Pi durante este tiempo, el nodo se reinicia.
 *
 * Es lo que le faltaba al 2026-11-10: a las 18:14 dejo de consultar y se
 * quedo asi quince horas. No se cayo y no se levanto — nunca estuvo
 * programado para levantarse. WATCHDOG_MS de arriba no reinicia nada,
 * desenergiza los reles y sigue consultando para siempre; sirve para la carpa
 * y no para el nodo.
 *
 * Cinco minutos. A esta altura los reles ya estan abiertos desde hace cuatro
 * por WATCHDOG_MS, asi que el reinicio no interrumpe nada que no este perdido
 * y sobran ~150 consultas falladas para descartar un hipo de WiFi.
 *
 * Cuenta desde el ultimo contacto HTTP, no desde la ultima consulta buena:
 * ver lastContact.
 */
static const unsigned long REBOOT_AFTER_MS = 300000;

/*
 * El watchdog de tarea del chip: el unico que puede sacar al nodo de un
 * cuelgue de verdad, uno en el que ni siquiera llega a contar los cinco
 * minutos de arriba. Un socket que no vuelve, la pila de WiFi trabada.
 *
 * Largo a proposito. Una vuelta lenta legitima puede tardar casi treinta
 * segundos (hasta 20 s de reintentos de WiFi, mas dos timeouts de 3 s del
 * HTTP, mas el delay del ciclo), asi que con 30 s el nodo se reiniciaria solo
 * por tener la red lenta. Noventa no se alcanzan nunca sin estar colgado.
 */
static const uint32_t TWDT_TIMEOUT_MS = 90000;

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
 * infrarrojo. Llegar tarde no cuesta nada; llegar temprano significa que el
 * pulso se pierde y el ciclo pasa sin niebla.
 *
 * Eran 3000 ms, puestos a ojo. El 2026-11-02 se midieron: cinco ciclos entre
 * las 12:22 y las 14:00 arrancaron bien, y los tres siguientes (14:15, 20:23 y
 * 21:27) no, con el tanque lleno y el infrarrojo sano. Ese mismo dia un POWER
 * mandado desde el panel, con el equipo energizado hacia rato, entro a la
 * primera y subio la carpa ocho puntos en un minuto. O sea que a 3 s el pulso
 * cae justo en el borde de cuando el receptor despierta: a veces gana la
 * carrera y a veces no, y la racha de cinco fue suerte.
 *
 * Diez segundos lo sacan de ese borde. El techo es el tiempo minimo de
 * encendido del lazo, 60 s, porque el pulso tiene que llegar con el rele
 * todavia cerrado: quedan 50 s de margen.
 *
 * Si aun asi se pierde, la Pi lo levanta: autocann/runtime/lost_pulse.py mira
 * la salida prendida sin que suba la humedad y aprieta el boton a los pocos
 * minutos. Eso es la red, no el arreglo; el arreglo es este numero.
 *
 * La granularidad real es la del lazo, asi que en la practica sale entre
 * IR_BOOT_DELAY_MS y un POLL_INTERVAL_MS mas tarde. Da igual: lo unico que
 * importa es no adelantarse.
 */
static const unsigned long IR_BOOT_DELAY_MS = 10000;

IRsend irsend(IR_LED_PIN);

// --------------------------------------------------------------------------

static bool applied[OUTPUT_COUNT];
static unsigned long lastGoodPoll = 0;
/*
 * Ultima vez que ALGO contesto del otro lado, aunque haya contestado mal.
 *
 * Distinto de lastGoodPoll a proposito, y la diferencia es la que decide si
 * reiniciarse sirve de algo. Si la Pi devuelve un 500, un 404, o el backend
 * esta arrancando, el camino de red esta sano y el roto no es este nodo:
 * reiniciarse no arregla nada y solo agrega churn. El reinicio es para cuando
 * no llega NADA, que es la unica falla que un reinicio puede tocar.
 */
static unsigned long lastContact = 0;
static unsigned long lastHeartbeat = 0;
static bool failsafeActive = true;   // hasta que la Pi diga otra cosa
static bool watchdogArmed = false;
static uint32_t wifiReconnects = 0;
static bool wifiWasConnected = false;
static const char *bootSlug = "unknown";
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

/*
 * Por que arranco, en una palabra sin espacios.
 *
 * Viaja en la consulta (ver poll), que es el unico canal que este nodo tiene
 * hacia la Pi. Es un slug y no la frase en castellano que usa el nodo de
 * sensores porque va en un query string: la Pi lo traduce, y asi las dos
 * placas terminan diciendo lo mismo en pantalla sin codificar nada aca.
 *
 * Es el dato que falto el 2026-11-10. Brownout, panic y watchdog mandan a
 * lugares distintos — la fuente, el codigo, un cuelgue — y sin esto las tres
 * se ven iguales: un nodo que no esta.
 */
static const char *resetReasonSlug() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON:  return "poweron";
    case ESP_RST_EXT:      return "ext";
    case ESP_RST_SW:       return "sw";
    case ESP_RST_PANIC:    return "panic";
    case ESP_RST_INT_WDT:  return "int_wdt";
    case ESP_RST_TASK_WDT: return "task_wdt";
    case ESP_RST_WDT:      return "wdt";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_SDIO:     return "sdio";
    default:               return "unknown";
  }
}

static void feedWatchdog() {
  if (watchdogArmed) esp_task_wdt_reset();
}

/*
 * Suscribe este loop al watchdog de tarea del chip.
 *
 * idle_core_mask en 0: no nos interesa vigilar las tareas idle, que es para lo
 * que el core de Arduino inicializa el TWDT — y lo hace con un timeout de
 * pocos segundos que a nosotros nos reiniciaria en cada espera de WiFi. Lo que
 * tiene que estar vigilado es este loop. Si el loop acapara el CPU tampoco va
 * a alimentarlo, asi que ese caso queda cubierto igual.
 *
 * Si el core ya lo inicializo, init devuelve ESP_ERR_INVALID_STATE y hay que
 * reconfigurarlo en vez de insistir.
 */
static void armWatchdog() {
  esp_task_wdt_config_t config = {
    .timeout_ms = TWDT_TIMEOUT_MS,
    .idle_core_mask = 0,
    .trigger_panic = true,
  };
  esp_err_t err = esp_task_wdt_init(&config);
  if (err == ESP_ERR_INVALID_STATE) err = esp_task_wdt_reconfigure(&config);
  if (err == ESP_OK) err = esp_task_wdt_add(NULL);
  watchdogArmed = (err == ESP_OK);
  Serial.printf("watchdog de tarea: %s (%lu ms)\n",
                watchdogArmed ? "armado" : "NO SE PUDO ARMAR",
                (unsigned long)TWDT_TIMEOUT_MS);
}

/*
 * Conectado Y con IP.
 *
 * WL_CONNECTED solo no alcanza: la station puede quedar asociada con la IP en
 * 0.0.0.0 — DHCP que no termino, asociacion a medias — y en la version
 * anterior eso salia por la primera linea de connectWifi y no se reintentaba
 * nunca mas. Un nodo "conectado" para siempre, sin red.
 */
static bool wifiUp() {
  return WiFi.status() == WL_CONNECTED && WiFi.localIP() != IPAddress(0, 0, 0, 0);
}

/*
 * Mantiene el WiFi, y cuando no vuelve baja la radio y la vuelve a levantar.
 *
 * Reintentar WiFi.begin() sobre una station trabada no la destraba: hay que
 * apagar el modo y volver a prenderlo. persistent(false) para no escribir la
 * NVS en cada reintento.
 *
 * Los reintentos se cuentan, porque un enlace que se cae cada dos minutos y
 * vuelve se ve igual que uno sano si solo se mira el estado de ahora; el
 * contador viaja en la consulta y queda en el panel.
 *
 * Lo que NO se toca es el power save del WiFi. setSleep(false) es el consejo
 * de siempre para un enlace que pierde beacons, pero son ~40 mA mas sobre una
 * fuente que es la principal sospechosa de haber tirado este nodo: hasta que
 * la alimentacion este separada de la placa de reles, agregar consumo es
 * empujar justo donde duele. Queda anotado como perilla, no aplicado.
 *
 * La espera alimenta el watchdog: son hasta 20 s y es el unico lugar donde una
 * vuelta del loop se estira de verdad.
 */
static void connectWifi() {
  if (wifiUp()) {
    wifiWasConnected = true;
    return;
  }

  if (wifiWasConnected) {
    wifiReconnects++;
    wifiWasConnected = false;
    Serial.printf("WiFi: se cayo el enlace (%lu veces)\n",
                  (unsigned long)wifiReconnects);
  }

  Serial.printf("WiFi: conectando a %s", WIFI_SSID);
  WiFi.persistent(false);
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  delay(200);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  for (int i = 0; i < WIFI_MAX_RETRIES && !wifiUp(); i++) {
    feedWatchdog();
    delay(500);
    Serial.print(".");
  }
  if (wifiUp()) {
    wifiWasConnected = true;
    Serial.println(" ok, ip " + WiFi.localIP().toString());
  } else {
    Serial.println(" sin conexion");
  }
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
 * Lee "ir":{"id":N,"code":"0x...","rescue":true}. Devuelve false si no hay
 * boton ("ir":null) o si no se entiende, y en ese caso no se manda nada.
 * "rescue" es opcional: sin el, es un boton comun.
 */
static bool findIrCommand(const String &body, uint32_t &id, uint32_t &code, bool &rescue) {
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
  rescue = ir.indexOf("\"rescue\":true") >= 0;
  return id != 0;
}

/*
 * Manda el boton del control remoto virtual, si hay uno nuevo.
 *
 * Un boton comun espera, sin confirmar, mientras el equipo esta arrancando (hay
 * un pulso de encendido pendiente) o sin energia: el pulso se perderia, y como
 * no se confirma la Pi lo vuelve a ofrecer en la consulta siguiente hasta que
 * vence.
 *
 * El RESCATE es lo contrario: la Pi lo manda cuando humidity_up esta apagado y
 * la humedad sube igual, o sea cuando el rele quedo pegado y el humidificador
 * sigue andando. Por eso sale justamente con la salida apagada. Si el rele se
 * habia soltado de verdad el equipo no tiene energia y el pulso no hace nada;
 * solo tiene efecto en el caso que se quiere cortar. Con la salida encendida
 * ya no corresponde (el lazo lo volvio a pedir) y se descarta sin mandarlo.
 */
static void serviceRemote(const String &body) {
  uint32_t id, code;
  bool rescue = false;
  if (!findIrCommand(body, id, code, rescue) || id == lastIrId) return;
  if (humidifierIndex >= OUTPUT_COUNT) return;

  bool powered = applied[humidifierIndex];
  if (rescue ? powered : (!powered || pulsePending)) return;

  irsend.sendNEC(code);
  lastIrId = id;
  Serial.printf("IR: %s, boton 0x%08lX (orden %lu)\n",
                rescue ? "RESCATE rele pegado" : "control remoto",
                (unsigned long)code, (unsigned long)id);
}

/* Una consulta. Devuelve true si se aplico un estado valido. */
static bool poll() {
  if (!wifiUp()) return false;

  HTTPClient http;
  http.setConnectTimeout(3000);
  http.setTimeout(3000);
  String url = String(API_URL) + (strchr(API_URL, '?') ? "&" : "?") + "ir_ack=" + lastIrId;
  /*
   * El nodo cuenta de si mismo en la consulta, porque no tiene otro canal.
   *
   * No postea nada — ni estado ni log — y agregarle un POST propio seria un
   * segundo temporizador que puede desincronizarse del primero, que es
   * justamente lo que el diseno de este nodo evita. La consulta ya va cada dos
   * segundos y ya es el latido: que lleve tambien el por que del ultimo
   * arranque, cuanto hace que esta de pie, la senal y los reintentos de WiFi.
   *
   * Con esos cuatro numeros la Pi distingue lo que el 2026-11-10 fue
   * indistinguible: un nodo sano, uno con la antena al limite, y uno que se
   * reinicia en bucle por brownout — que consulta bien entre reinicio y
   * reinicio, asi que por antiguedad de la consulta se ve igual que sano.
   */
  url += "&boot=" + String(bootSlug);
  url += "&up=" + String(millis() / 1000);
  url += "&rssi=" + String(WiFi.RSSI());
  url += "&rc=" + String(wifiReconnects);
  http.begin(url);
  int status = http.GET();
  // Contesto algo, aunque haya contestado mal: el camino de red esta sano y
  // reiniciarse no corresponde. Un status negativo es error de conexion.
  if (status > 0) lastContact = millis();
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
  Serial.printf("consulta cada %lu ms, reles abiertos a los %lu ms, "
                "reinicio a los %lu ms\n",
                POLL_INTERVAL_MS, WATCHDOG_MS, REBOOT_AFTER_MS);

  bootSlug = resetReasonSlug();
  Serial.printf("arranque: %s\n", bootSlug);
  armWatchdog();

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
  // Arrancan los dos relojes ahora: si la Pi no contesta nunca, tanto los
  // reles como el reinicio tienen que vencer igual en vez de quedarse
  // esperando una primera consulta buena que no va a llegar.
  lastGoodPoll = millis();
  lastContact = millis();
}

void loop() {
  feedWatchdog();
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

  /*
   * Y si no contesta NADA, reiniciarse.
   *
   * Es la unica cosa que puede destrabar la radio o la pila de red de este
   * nodo, y la que convierte quince horas en cinco minutos. Los reles ya estan
   * abiertos desde hace cuatro minutos por el bloque de arriba, asi que los
   * tres segundos de arranque no interrumpen nada que no este perdido — y se
   * los deja explicitamente abiertos antes de reiniciar, porque el latch tiene
   * que valer OFF cuando los pines se reinicializen.
   *
   * Si vuelve a pasar, la Pi se va a enterar: el proximo arranque viaja en la
   * primera consulta como boot=sw, y un bucle de reinicios se ve en el panel
   * aunque las consultas lleguen puntuales entre uno y otro.
   *
   * Lo que esto no puede arreglar es la falta de energia. Si la fuente se cae,
   * no hay firmware que lo levante.
   */
  if (millis() - lastContact > REBOOT_AFTER_MS) {
    Serial.printf("** SIN NINGUN CONTACTO %lu s: me reinicio\n",
                  (millis() - lastContact) / 1000);
    allOff("reinicio por falta de contacto");
    Serial.flush();
    delay(100);
    ESP.restart();
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
