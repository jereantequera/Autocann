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

export { escapeHtml, fmtNum, parseLocalDatetime, formatDate, getScoreClass, getScoreLabel };
