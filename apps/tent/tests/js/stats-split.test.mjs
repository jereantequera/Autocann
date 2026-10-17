/**
 * Tests for the day and night second line of the stat tiles
 * (static/js/stats-split.js).
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    rangeText, splitLine, summarySplitLines,
} from '../../autocann/web/static/js/stats-split.js';

const summary = {
    temperature: { avg: 22.4, min: 18, max: 27 },
    humidity: { avg: 61.2 },
    day: {
        temperature: { avg: 24.1, min: 21, max: 27 },
        humidity: { avg: 58.6 },
        time_in_range: { percent: 78, in_range: 78, total: 100 },
        sample_count: 216,
    },
    night: {
        temperature: { avg: 21.3, min: 18, max: 23 },
        humidity: { avg: 66.0 },
        time_in_range: { percent: 41, in_range: 41, total: 100 },
        sample_count: 72,
    },
};

test('the temperature line reads Día and Noche with a decimal comma', () => {
    assert.equal(summarySplitLines(summary)['summary-temp-split'], 'Día 24,1 · Noche 21,3');
});

test('the humidity line uses the same shape', () => {
    assert.equal(summarySplitLines(summary)['summary-humidity-split'], 'Día 58,6 · Noche 66,0');
});

test('the time in range line is a whole percentage for each part', () => {
    assert.equal(summarySplitLines(summary)['summary-in-range-split'], 'Día 78 % · Noche 41 %');
});

test('a percentage rounds to the nearest whole number', () => {
    const rounded = {
        day: { time_in_range: { percent: 77.6 } },
        night: { time_in_range: { percent: 41.2 } },
    };
    assert.equal(summarySplitLines(rounded)['summary-in-range-split'], 'Día 78 % · Noche 41 %');
});

test('a missing side reads as the dash, not as a zero', () => {
    assert.equal(splitLine(24.1, null, v => v.toFixed(1)), 'Día 24.1 · Noche --');
    assert.equal(splitLine(undefined, 21.3, v => v.toFixed(1)), 'Día -- · Noche 21.3');
});

test('nothing on either side means no line at all', () => {
    assert.equal(splitLine(null, undefined, String), '');
    assert.equal(splitLine(NaN, NaN, String), '');
});

test('a summary with no day or night clears every split line', () => {
    const lines = summarySplitLines({ sample_count: 0 });
    assert.deepEqual(lines, {
        'summary-temp-split': '',
        'summary-humidity-split': '',
        'summary-in-range-split': '',
    });
});

test('a window across every grow has no time in range split', () => {
    const allGrows = {
        day: { temperature: { avg: 20 }, time_in_range: null },
        night: { temperature: { avg: 18 }, time_in_range: null },
    };
    assert.equal(summarySplitLines(allGrows)['summary-in-range-split'], '');
    assert.equal(summarySplitLines(allGrows)['summary-temp-split'], 'Día 20,0 · Noche 18,0');
});

test('the range text keeps its dash when either end is missing', () => {
    assert.equal(rangeText(18, 26, 1), '18,0 – 26,0');
    assert.equal(rangeText(null, 26, 1), '--');
    assert.equal(rangeText(18, undefined, 1), '--');
});
