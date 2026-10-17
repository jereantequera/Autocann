/**
 * The day and night second line of the Estadísticas stat tiles.
 *
 * Pure: no DOM and no network. The period summary carries `day` and `night`
 * sub-summaries (see store/day_night.py); this turns them into the text of the
 * compact line under each tile, "Día 24,1 · Noche 21,3". dashboard.js writes
 * the text; this module only decides it.
 */

import { fmtComma } from './util.js';

/** "18,0 – 26,0", or the dash when either end is missing. */
function rangeText(min, max, decimals) {
    if (!Number.isFinite(min) || !Number.isFinite(max)) return '--';
    return `${fmtComma(min, decimals)} – ${fmtComma(max, decimals)}`;
}

/**
 * "Día 24,1 · Noche 21,3" for two numbers. A side with no number reads as the
 * dash, so one missing half is visible rather than silently dropped; when both
 * are missing there is no line at all.
 *
 * @param {number|null|undefined} day
 * @param {number|null|undefined} night
 * @param {(value: number) => string} format  how one number is written
 * @returns {string}
 */
function splitLine(day, night, format) {
    if (!Number.isFinite(day) && !Number.isFinite(night)) return '';
    const dayText = Number.isFinite(day) ? format(day) : '--';
    const nightText = Number.isFinite(night) ? format(night) : '--';
    return `Día ${dayText} · Noche ${nightText}`;
}

const plain = value => fmtComma(value, 1);
const percent = value => `${Math.round(value)} %`;

/**
 * The text for each split line in the stats header, by element id.
 *
 * A summary without day and night (an empty period, or a window across every
 * grow for time in range, which has no single band) gives empty text, so the
 * line is cleared rather than left showing the previous period.
 *
 * @param {object} data  the /api/period-summary response
 * @returns {Record<string, string>}
 */
function summarySplitLines(data) {
    const day = data?.day;
    const night = data?.night;
    return {
        'summary-temp-split': splitLine(day?.temperature?.avg, night?.temperature?.avg, plain),
        'summary-humidity-split': splitLine(day?.humidity?.avg, night?.humidity?.avg, plain),
        'summary-in-range-split': splitLine(
            day?.time_in_range?.percent, night?.time_in_range?.percent, percent),
    };
}

export { rangeText, splitLine, summarySplitLines };
