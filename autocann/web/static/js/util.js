/**
 * Pure helpers: no DOM, no network, no module state.
 *
 * Everything here is a plain input/output function, which is what makes it
 * testable on its own (see tests/js/).
 */

function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// `x ? fmt(x) : '--'` renders a real 0 as "--". 0 °C of leaf temperature
// and 0% of target humidity are both legitimate readings.

function fmtNum(value, decimals = 1, fallback = '--') {
    return (typeof value === 'number' && Number.isFinite(value))
        ? value.toFixed(decimals)
        : fallback;
}

// ===============================
// Connection state
// ===============================
// The header used to contain a hardcoded "Sistema Activo", which said the
// same thing with the backend down.

function parseLocalDatetime(value) {
    if (value instanceof Date) return value;
    if (typeof value !== 'string') return new Date(NaN);
    return new Date(value.trim().replace(' ', 'T'));
}

/**
 * Axis label for a sample's datetime.
 *
 * The granularity is taken as an argument rather than read from shared state, so
 * this stays a pure function: same inputs, same output, testable in isolation.
 *
 * @param {string} dateString  "YYYY-MM-DD HH:MM:SS" or ISO
 * @param {{hours: ?number, interval: ?string}} view  the selected time range
 */
function formatDate(dateString, view = {}) {
    const date = parseLocalDatetime(dateString);
    if (isNaN(date.getTime())) return String(dateString ?? '--');
    const day = date.getDate().toString().padStart(2, '0');
    const month = (date.getMonth() + 1).toString().padStart(2, '0');
    const hours = date.getHours().toString().padStart(2, '0');
    const minutes = date.getMinutes().toString().padStart(2, '0');

    if (view.hours !== null && view.hours !== undefined && view.hours < 24) {
        return `${hours}:${minutes}`;
    }
    if (view.interval === 'daily') {
        return `${day}/${month}`;
    }
    return `${day}/${month} ${hours}:${minutes}`;
}

/**
 * How long ago a reading was taken, in words.
 *
 * Takes an age in seconds rather than a timestamp, and the age is the server's:
 * the Raspberry has no real-time clock, so it and the browser can disagree by
 * minutes, and a browser-side subtraction renders a live sensor as "hace -3 min".
 */
function formatAge(seconds) {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return 'sin datos';
    if (seconds <= 0) return 'ahora';
    if (seconds < 60) return `hace ${Math.round(seconds)} s`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `hace ${minutes} min`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `hace ${hours} h ${minutes % 60} min`;
    return `hace ${Math.floor(hours / 24)} d`;
}

/** Una duración que falta, en palabras: "7 h 59 min", "45 min", "30 s". */
function formatDuration(seconds) {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) return '--';
    const total = Math.round(seconds);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (hours > 0) return `${hours} h ${minutes} min`;
    if (minutes > 0) return `${minutes} min`;
    return `${total} s`;
}

/** A signed difference, so "+0.4" and "-0.4" are told apart at a glance. */
function fmtSigned(value, decimals = 1, fallback = '--') {
    if (typeof value !== 'number' || !Number.isFinite(value)) return fallback;
    return (value >= 0 ? '+' : '') + value.toFixed(decimals);
}

/**
 * El cartel de diferencia contra la humedad objetivo, o null si falta una lectura.
 *
 * Nullish no es null: cuando /api/current-data responde 404 el cuerpo
 * `{"error": ...}` llega igual, cada campo vale `undefined`, y un guard escrito
 * `humidity !== null` lo deja pasar y rinde "NaN% por encima" en pantalla.
 *
 * @returns {?{state: string, text: string}}  null cuando no hay nada que comparar
 */
function humidityDiffLabel(humidity, targetHumidity, tolerance = 3) {
    if (!Number.isFinite(humidity) || !Number.isFinite(targetHumidity)) return null;
    const diff = humidity - targetHumidity;
    const absDiff = Math.abs(diff).toFixed(1);
    if (Math.abs(diff) <= tolerance) return { state: 'on-target', text: '✓ En objetivo' };
    if (diff < 0) return { state: 'too-low', text: `↓ ${absDiff}% por debajo` };
    return { state: 'too-high', text: `↑ ${absDiff}% por encima` };
}

/**
 * El borne que hay que ir a medir para una salida: el número corto para la
 * tarjeta, y la frase entera para el title.
 *
 * Decía "BCM 7" siempre, y con `AUTOCANN_RELAY_MODE=remote` la Raspberry no
 * conmuta nada: eran tres pines de una placa que no está en el circuito. Peor
 * todavía, el 25 que mostraba para la ventilación existe también en el ESP32,
 * donde maneja el humidificador — así que mandaba a medir el header correcto
 * con la etiqueta equivocada.
 *
 * @param {?{scope: string, node: ?string, number: ?number}} source  de /api/output-status
 * @returns {{text: string, title: string}}  vacío cuando no hay pin que mostrar
 */
function outputPinLabel(source) {
    // Sin coerción: `Number(null)` es 0, y el BCM 0 es un pin que existe. Una
    // salida sin pin rendiría "BCM 0", que no se lee como un hueco sino como
    // un dato — justo el defecto que esto viene a sacar de la pantalla.
    const number = source?.number;
    if (!Number.isFinite(number)) return { text: '', title: '' };
    if (source.scope === 'remote') {
        const node = source.node || 'el nodo remoto';
        return {
            text: `GPIO ${number}`,
            title: `La conmuta el GPIO ${number} de ${node}, no la Raspberry`,
        };
    }
    return {
        text: `BCM ${number}`,
        title: `La conmuta el BCM ${number} de la Raspberry`,
    };
}

/**
 * Qué placa maneja los relés, para el encabezado del panel.
 *
 * Va una vez arriba y no en cada tarjeta porque las tres salidas están siempre
 * en la misma placa, y el panel se mide para entrar en una pantalla de 1080p
 * sin scroll: tres renglones repetidos cuestan alto que no hay.
 *
 * @returns {{text: string, title: string}}
 */
function relaySourceLabel(source) {
    if (source?.scope === 'remote') {
        const node = source.node || 'un nodo remoto';
        return {
            text: node,
            title: `Los relés los maneja ${node}: los pines que se muestran son GPIO `
                + 'de esa placa, no de la Raspberry',
        };
    }
    if (source?.scope === 'local') {
        return {
            text: 'GPIO de la Raspberry',
            title: 'Los relés los maneja el GPIO de la propia Raspberry, en numeración BCM',
        };
    }
    return { text: '', title: '' };
}

function getScoreClass(score) {
    if (score >= 85) return 'excellent';
    if (score >= 70) return 'good';
    if (score >= 50) return 'fair';
    return 'poor';
}

function getScoreLabel(score) {
    if (score >= 85) return '🏆 Excelente';
    if (score >= 70) return '✅ Bueno';
    if (score >= 50) return '⚠️ Regular';
    return '❌ Necesita mejorar';
}

export {
    escapeHtml, fmtNum, fmtSigned, formatAge, formatDuration, parseLocalDatetime,
    formatDate, humidityDiffLabel, outputPinLabel, relaySourceLabel,
    getScoreClass, getScoreLabel,
};
