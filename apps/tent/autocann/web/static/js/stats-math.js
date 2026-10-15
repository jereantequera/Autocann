/**
 * Pure helpers for Estadísticas and Reportes: no DOM, no network, no state.
 *
 * They live apart from analytics.js and dashboard.js because those modules
 * touch the document when they load, so they cannot be imported by the node
 * tests in tests/js/.
 */

const MONTHS = [
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
];

const DAY_NAMES = ['Domingo', 'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado'];

/** The same thresholds the VPD score card already states: 70 and 40. */
const TONE_GOOD = 70;
const TONE_MIXED = 40;

/** "2026-10-09" -> { year, month (0-11), day }, or null if it is not a date. */
function parseIsoDay(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value ?? ''));
    if (!match) return null;
    const [year, month, day] = match.slice(1).map(Number);
    if (month < 1 || month > 12 || day < 1 || day > 31) return null;
    return { year, month: month - 1, day };
}

/**
 * The heading of the weekly report, in the words the grower reads.
 *
 * "Semana del 9 al 15 de octubre" while both days share a month; the month
 * is said twice when the week crosses one, so neither end reads as the other.
 */
function weekLabel(start, end) {
    const a = parseIsoDay(start);
    const b = parseIsoDay(end);
    if (!a || !b) return null;
    if (a.month === b.month && a.year === b.year) {
        return `Semana del ${a.day} al ${b.day} de ${MONTHS[b.month]}`;
    }
    return `Semana del ${a.day} de ${MONTHS[a.month]} al ${b.day} de ${MONTHS[b.month]}`;
}

/**
 * Share of the samples whose VPD sat inside the band of their own stage.
 *
 * The leaf reading is what the band is about, and the air reading stands in
 * only when a row has no leaf sensor, which is how the backend's VPD score
 * decides it too. Each row is judged against its own stage, so one list that
 * spans two grows is still judged correctly.
 *
 * @param {Array<{leaf_vpd?: number|null, vpd?: number|null, stage?: string}>} samples
 * @param {(stage: string|undefined) => ({min: number, max: number}|undefined)} rangeFor
 * @returns {{inRange: number, total: number, percent: number|null}}
 */
function timeInRange(samples, rangeFor) {
    let inRange = 0;
    let total = 0;
    for (const sample of samples || []) {
        const value = Number.isFinite(sample?.leaf_vpd) ? sample.leaf_vpd : sample?.vpd;
        const range = rangeFor(sample?.stage);
        if (!Number.isFinite(value) || !range) continue;
        total += 1;
        if (value >= range.min && value <= range.max) inRange += 1;
    }
    return {
        inRange,
        total,
        percent: total > 0 ? Math.round((inRange / total) * 100) : null,
    };
}

/** Where a bar's top sits, as a percentage of its column; empty is zero. */
function barHeightPct(score) {
    if (!Number.isFinite(score)) return 0;
    return Math.min(100, Math.max(0, score));
}

/**
 * The colour class of a score: good, mixed or low.
 *
 * "empty" is its own answer, not "low": a missing hour is not a bad hour, and
 * a bar drawn for it would claim a reading that was never taken.
 */
function scoreTone(score) {
    if (!Number.isFinite(score)) return 'empty';
    if (score >= TONE_GOOD) return 'good';
    if (score >= TONE_MIXED) return 'mixed';
    return 'low';
}

/**
 * One hour's bar for the 24-column chart.
 *
 * @param {number|null|undefined} score  the hour's VPD score, 0-100
 * @returns {{height: number, tone: string}}
 */
function hourBar(score) {
    return { height: barHeightPct(score), tone: scoreTone(score) };
}

/**
 * Fills 24 slots from the report's hourly list, keyed by hour.
 *
 * The backend only lists hours that had samples, so an absent hour is left as
 * an empty slot rather than collapsing the column layout.
 */
function hourlySlots(hourly) {
    const byHour = new Map();
    for (const entry of hourly || []) {
        if (Number.isInteger(entry?.hour)) byHour.set(entry.hour, entry.vpd_score ?? null);
    }
    return Array.from({ length: 24 }, (_, hour) => ({
        hour,
        score: byHour.has(hour) ? byHour.get(hour) : null,
    }));
}

/**
 * The change against the previous week, as the text and the colour the trend
 * chip shows. For the VPD score an increase is good; for temperature and
 * humidity a change is only a change, so it carries no colour of its own.
 *
 * @returns {{tone: 'up'|'down'|'flat'|'none', text: string}}
 */
function trendText(value, unit, { invertColors = false } = {}) {
    if (!Number.isFinite(value)) return { tone: 'none', text: 'sin semana anterior' };
    if (value === 0) return { tone: 'flat', text: 'igual que la anterior' };
    const up = value > 0;
    const magnitude = String(Math.abs(value)).replace('.', ',');
    const sign = up ? '+' : '−';
    let tone = up ? 'up' : 'down';
    if (!invertColors) tone = 'none';
    return { tone, text: `${sign}${magnitude}${unit} vs. anterior` };
}

/** "#E8B07A" at 0.13 -> "rgba(232, 176, 122, 0.13)"; anything else is returned as is. */
function withAlpha(color, alpha) {
    const match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(color ?? '').trim());
    if (!match) return color;
    const [r, g, b] = match.slice(1).map(hex => parseInt(hex, 16));
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** Spanish weekday name for an ISO day, without the timezone shifting it. */
function dayName(isoDay) {
    const parsed = parseIsoDay(isoDay);
    if (!parsed) return '';
    const weekday = new Date(Date.UTC(parsed.year, parsed.month, parsed.day)).getUTCDay();
    return DAY_NAMES[weekday];
}

export {
    weekLabel, timeInRange, barHeightPct, scoreTone, hourBar, hourlySlots,
    trendText, withAlpha, dayName, parseIsoDay,
};
