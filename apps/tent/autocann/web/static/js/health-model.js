/**
 * The health model: the sensor roles and states, and the pure decisions that
 * turn /api/health into the checks and the badges. No DOM and no fetches, so
 * the unit tests can run it under node.
 */

// ===============================
// Vocabulario
// ===============================
// The tent has two DHT22 on one node: a primary that drives control and a
// witness that drives nothing. The witness exists to catch the primary
// reporting a plausible wrong number, so it is only worth having if somebody
// can see it - which is what this section is.

export const SENSOR_ROLES = {
    primary: { icon: 'crosshair', label: 'Primario, controla' },
    witness: { icon: 'eye', label: 'Testigo, sólo vigila' },
    // Shown and nothing else. The sensor above the lamp is worth reading and
    // useless as a witness: its offset against the primary moves with the
    // photoperiod, not with the sensors' health.
    observer: { icon: 'chart-line', label: 'Observación, sólo informa' },
};

/** Same wording the control loop logs, so the two are recognisably the same fault. */
export const DRIFT_FLAG_LABELS = {
    stuck: 'valor congelado',
    railed: 'pegado al tope de escala',
};

/**
 * Cómo se ve cada estado.
 *
 * Cuatro estados, no dos. "Nunca posteó" y "dejó de postear" se ven iguales en
 * una tarjeta que sólo sabe ok/error, y mandan a lugares distintos: la primera
 * al id del firmware y al cableado, la segunda a la red y al log del nodo. Y
 * "postea pero el detector lo marcó" es el que más esconde un badge verde.
 */
export const SENSOR_STATES = {
    posting: { dot: 'ac-dot--ok', badge: 'ok' },
    suspect: { dot: 'ac-dot--warn', badge: 'stale' },
    stale: { dot: 'ac-dot--alert', badge: 'error' },
    missing: { dot: 'ac-dot--alert', badge: 'error' },
};

// ===============================
// Helpers puros
// ===============================

/**
 * El badge de la sección: cuántos sensores están posteando, de cuántos.
 *
 * Un número, no un tilde: "2 de 2 posteando" sigue siendo verdad cuando alguien
 * agrega un tercer sensor y se olvida de configurarlo, y un tilde verde no.
 */
export function sensorsBadge(sensors) {
    if (!sensors || !sensors.length) return { text: 'sin sensores', state: '' };
    const live = sensors.filter(s => s.state === 'posting' || s.state === 'suspect').length;
    return {
        icon: live === sensors.length ? 'circle-check' : live ? 'triangle-alert' : 'circle-x',
        text: `${live} de ${sensors.length} posteando`,
        state: live === sensors.length ? 'ok' : live ? 'stale' : 'error',
    };
}

/**
 * Los cuatro chequeos de la sección Estado, cada uno con su nivel.
 *
 * Backend viene del propio cliente HTTP, no de /api/health: si el backend no
 * contesta, el veredicto tampoco puede llegar, y el chequeo tiene que decirlo.
 */
export function healthChecks(health, { offline = false } = {}) {
    const sensors = health?.sensors || [];
    const sensorsSummary = sensorsBadge(sensors);
    return [
        {
            name: 'Backend',
            level: offline ? 'error' : 'ok',
            detail: offline ? 'sin respuesta' : 'responde',
        },
        {
            name: 'Lazo de control',
            level: health?.loop?.level || 'idle',
            detail: health?.loop?.label || 'sin datos',
        },
        {
            name: 'Sensores',
            level: sensorsSummary.state === 'ok' ? 'ok' : sensorsSummary.state === 'stale' ? 'warn'
                : sensorsSummary.state === 'error' ? 'error' : 'idle',
            detail: sensorsSummary.text,
        },
        {
            name: 'Histórico',
            level: health?.history?.level || 'idle',
            detail: health?.history?.label || 'sin datos',
        },
    ];
}

/**
 * La frase grande de arriba de Sistema.
 *
 * "Todo anda" sólo cuando los cuatro chequeos están en ok. Si no, lo que dice
 * el backend sobre lo peor que está pasando: nombrarlo, no contar problemas.
 */
export function verdictFor(health, checks) {
    if (!health) return 'Consultando el estado';
    if (health.error) return 'No se pudo calcular el estado';
    if (checks.every(check => check.level === 'ok')) return 'Todo anda';
    return health.headline || 'Hay algo para revisar';
}
