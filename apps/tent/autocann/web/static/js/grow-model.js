/**
 * Pure helpers behind the Cultivo view: the stage bar, the grow header and the
 * grow table. Kept free of the DOM and of the feature modules so the arithmetic
 * can be tested with node alone (tests/js/grows.test.mjs).
 */

import { fmtComma } from './util.js';

const MONTHS = [
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
];

const DAY_MS = 86400000;

/** The stage after `current` in the cycle, or null at the end of it (or unknown). */
function nextStage(order, current) {
    const index = order.indexOf(current);
    if (index < 0 || index === order.length - 1) return null;
    return order[index + 1];
}

/**
 * "YYYY-MM-DD" or "YYYY-MM-DD HH:MM:SS" as a local calendar day, without going
 * through Date parsing: `new Date('2026-10-15')` is UTC midnight, which is the
 * 14th in Buenos Aires.
 */
function parseDay(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value ?? '').trim());
    if (!match) return null;
    return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

/** "15/10/2026", as the grow table reads it. */
function formatShortDate(value) {
    const day = parseDay(value);
    if (!day) return '--';
    const pad = number => String(number).padStart(2, '0');
    return `${pad(day.getDate())}/${pad(day.getMonth() + 1)}/${day.getFullYear()}`;
}

/** "15 de octubre", as the Cultivo header reads it. */
function formatLongDate(value) {
    const day = parseDay(value);
    if (!day) return '--';
    return `${day.getDate()} de ${MONTHS[day.getMonth()]}`;
}

/**
 * How many calendar days a grow has run, counting its first day as day 1.
 * An open grow runs until `now`; a finished one until its end date.
 */
function growDays(startDate, endDate, now = new Date()) {
    const start = parseDay(startDate);
    if (!start) return null;
    const end = parseDay(endDate) || new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const days = Math.round((end - start) / DAY_MS) + 1;
    return days > 0 ? days : 1;
}

function durationLabel(days) {
    if (!Number.isFinite(days)) return '--';
    return days === 1 ? '1 día' : `${days} días`;
}

/** "0,6 – 1,0 kPa" from a {min, max} band; null when the stage has none. */
function vpdLabel(range) {
    if (!range || !Number.isFinite(range.min) || !Number.isFinite(range.max)) return null;
    return `${fmtComma(range.min)} – ${fmtComma(range.max)} kPa`;
}

/**
 * The header line under the grow's name:
 * "Vegetativo temprano · día 1 de ~21 · VPD objetivo 0,6 – 1,0 kPa".
 * Each part drops out when the server has not got it.
 */
function growLine({ stageName, day, expected, vpd }) {
    const parts = [stageName || '--'];
    if (Number.isFinite(day)) {
        parts.push(Number.isFinite(expected) && expected > 0
            ? `día ${day} de ~${expected}` : `día ${day}`);
    }
    const band = vpdLabel(vpd);
    if (band) parts.push(`VPD objetivo ${band}`);
    return parts.join(' · ');
}

/**
 * One column per stage of the cycle, for the stage bar.
 *
 * Widths follow the expected length of each stage, as the design asks, but only
 * when every stage's length is known. Until the server says them all, the bar
 * is equal columns: a wrong proportion would read as a real forecast.
 *
 * @param {string[]} order                 stages in cycle order, from /api/config
 * @param {?Object<string, number>} expected  days per stage, or null
 * @returns {number[]} one flex weight per stage
 */
function stageWeights(order, expected) {
    const days = order.map(stage => expected?.[stage]);
    const known = days.every(value => Number.isFinite(value) && value > 0);
    return known ? days : order.map(() => 1);
}

/**
 * What each stage cell of the bar shows.
 *
 * - done: before the current stage; a full grey bar.
 * - now: the current stage, filled to day / expected.
 * - next: after it; an empty bar.
 *
 * A done stage that the grow never entered (it was skipped) says so instead
 * of showing a zero.
 *
 * @param {object} input
 * @param {string[]} input.order
 * @param {?string} input.current       the grow's stage
 * @param {?number} input.dayInStage
 * @param {?Object<string, number>} input.expected
 * @param {?Array<{stage: string, days: number}>} input.timeline
 * @param {Object<string, string>} input.names
 */
function stageBarModel({ order, current, dayInStage, expected, timeline, names }) {
    const weights = stageWeights(order, expected);
    const currentIndex = order.indexOf(current);
    const lengthOf = stage => (Number.isFinite(expected?.[stage]) ? expected[stage] : null);

    return order.map((stage, index) => {
        const base = {
            stage,
            name: names?.[stage] || stage,
            flex: weights[index],
        };

        if (currentIndex >= 0 && index < currentIndex) {
            const period = (timeline || []).find(item => item.stage === stage);
            return {
                ...base,
                state: 'done',
                fill: 100,
                meta: Number.isFinite(period?.days) ? `${period.days} d` : 'salteada',
            };
        }

        if (index === currentIndex) {
            const length = lengthOf(stage);
            const hasDay = Number.isFinite(dayInStage);
            const fill = hasDay && length
                ? Math.min((dayInStage / length) * 100, 100) : 0;
            let meta = '--';
            if (hasDay) meta = length ? `día ${dayInStage} de ~${length}` : `día ${dayInStage}`;
            return { ...base, state: 'now', fill, meta };
        }

        const length = lengthOf(stage);
        return {
            ...base,
            state: 'next',
            fill: 0,
            meta: length ? `~${length} d` : '',
        };
    });
}

export {
    nextStage, formatShortDate, formatLongDate, growDays, durationLabel,
    vpdLabel, growLine, stageWeights, stageBarModel,
};
