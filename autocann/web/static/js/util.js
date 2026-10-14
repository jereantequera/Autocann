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

// ===============================
// Feeding
// ===============================
// The panel tile and the Riego tab render the same plan from the same payload,
// so the arithmetic and the wording live here rather than once in each: two
// copies of "25 ml" is how one of them ends up rounding differently.

/** 1 -> "1", 1.5 -> "1,5". Doses are read aloud, not computed with. */
function fmtDose(value) {
    if (typeof value !== 'number' || !Number.isFinite(value)) return '--';
    return Number.isInteger(value) ? String(value) : value.toFixed(1).replace('.', ',');
}

/**
 * What actually goes in the watering can: the per-litre dose times the volume.
 *
 * Rounded to a tenth because nobody measures a fertiliser finer than that -
 * the same rounding the backend applies to `total`, so a locally recomputed
 * amount and a served one agree.
 */
function doseAmount(item, litres) {
    const total = (Number(item?.dose) || 0) * (Number(litres) || 0);
    return `${fmtDose(Math.round(total * 10) / 10)} ${item?.unit === 'g/L' ? 'g' : 'ml'}`;
}

/** Days before an unwatered tent is worth saying out loud. */
const STALE_AFTER_DAYS = 4;

/**
 * How long since the last watering, in words, from its "YYYY-MM-DD".
 *
 * Measured midnight to midnight rather than now minus then: a watering logged
 * this morning is "hoy" all day, and one logged last night does not become
 * "hace 2 días" the moment the clock passes the hour it happened at.
 *
 * @returns {?{days: number, text: string, stale: boolean}}  null with no log
 */
function lastWateringLabel(date) {
    if (!date) return null;
    const then = new Date(`${date}T00:00:00`);
    if (isNaN(then.getTime())) return null;

    const midnight = new Date();
    midnight.setHours(0, 0, 0, 0);
    const days = Math.round((midnight - then) / 86400000);

    const text = days <= 0 ? 'Hoy'
        : days === 1 ? 'Ayer'
            : `Hace ${days} días`;
    return { days, text, stale: days >= STALE_AFTER_DAYS };
}

// ===============================
// Grow scope
// ===============================
// Which grow the history is about travels in the query string, and the absence
// of the parameter means "the active one". Building it here rather than at each
// call site keeps a selector set to "todos" from turning into `grow_id=null` in
// a URL — which the backend rejects, so the chart would just go blank.

/** The `grow_id` parameter for a scope, or '' when the default is wanted. */
function growParam(growId) {
    if (growId === null || growId === undefined || growId === '') return '';
    if (growId === 'all') return 'grow_id=all';
    const id = Number(growId);
    return Number.isInteger(id) ? `grow_id=${id}` : '';
}

/** `url` with the grow scope appended, whichever separator it needs. */
function withGrow(url, growId) {
    const param = growParam(growId);
    if (!param) return url;
    return `${url}${url.includes('?') ? '&' : '?'}${param}`;
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
    formatDate, growParam, humidityDiffLabel, getScoreClass, getScoreLabel, withGrow,
    STALE_AFTER_DAYS, fmtDose, doseAmount, lastWateringLabel,
};
