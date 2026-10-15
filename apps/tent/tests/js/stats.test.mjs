/**
 * Tests for the pure helpers behind Estadísticas and Reportes
 * (static/js/stats-math.js): the time-in-range share, the bar heights and
 * colours of the hourly and daily charts, and the week's heading.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    barHeightPct, dayName, hourBar, hourlySlots, parseIsoDay, scoreTone,
    timeInRange, trendText, weekLabel, withAlpha,
} from '../../autocann/web/static/js/stats-math.js';

const ranges = {
    seedling: { min: 0.4, max: 0.8 },
    early_veg: { min: 0.6, max: 1.0 },
};
const rangeFor = stage => ranges[stage];

test('time in range counts the samples whose leaf VPD sits in the stage band', () => {
    const samples = [
        { leaf_vpd: 0.7, vpd: 1.4, stage: 'early_veg' }, // in
        { leaf_vpd: 1.2, vpd: 0.8, stage: 'early_veg' }, // out: the leaf reading wins
        { leaf_vpd: 0.6, vpd: null, stage: 'early_veg' }, // in, on the lower edge
        { leaf_vpd: null, vpd: 0.9, stage: 'early_veg' }, // in, air stands in
    ];
    assert.deepEqual(timeInRange(samples, rangeFor), { inRange: 3, total: 4, percent: 75 });
});

test('each sample is judged against its own stage, so two grows can share a list', () => {
    const samples = [
        { vpd: 0.5, stage: 'seedling' }, // in the seedling band only
        { vpd: 0.5, stage: 'early_veg' }, // out of the early-veg band
    ];
    assert.equal(timeInRange(samples, rangeFor).inRange, 1);
});

test('samples with no VPD or no known stage are left out, not counted as out', () => {
    const samples = [
        { vpd: 0.7, stage: 'early_veg' },
        { vpd: null, stage: 'early_veg' },
        { vpd: 0.7, stage: 'unknown' },
    ];
    assert.deepEqual(timeInRange(samples, rangeFor), { inRange: 1, total: 1, percent: 100 });
});

test('no judgeable samples is no percentage at all, not zero', () => {
    assert.deepEqual(timeInRange([], rangeFor), { inRange: 0, total: 0, percent: null });
    assert.equal(timeInRange(null, rangeFor).percent, null);
});

test('bar heights are the score, clamped to the column', () => {
    assert.equal(barHeightPct(58), 58);
    assert.equal(barHeightPct(140), 100);
    assert.equal(barHeightPct(-3), 0);
    assert.equal(barHeightPct(null), 0);
    assert.equal(barHeightPct(undefined), 0);
});

test('the colour thresholds are 70 and 40, and a missing hour is its own tone', () => {
    assert.equal(scoreTone(70), 'good');
    assert.equal(scoreTone(69.9), 'mixed');
    assert.equal(scoreTone(40), 'mixed');
    assert.equal(scoreTone(39), 'low');
    assert.equal(scoreTone(null), 'empty');
    assert.deepEqual(hourBar(21), { height: 21, tone: 'low' });
    assert.deepEqual(hourBar(null), { height: 0, tone: 'empty' });
});

test('hourly slots always have 24 entries, and missing hours stay empty', () => {
    const slots = hourlySlots([{ hour: 3, vpd_score: 96 }, { hour: 13, vpd_score: 21 }]);
    assert.equal(slots.length, 24);
    assert.deepEqual(slots[3], { hour: 3, score: 96 });
    assert.deepEqual(slots[0], { hour: 0, score: null });
    assert.equal(slots[23].hour, 23);
});

test('the week heading names both days, and the month once when it is the same', () => {
    assert.equal(weekLabel('2026-10-09', '2026-10-15'), 'Semana del 9 al 15 de octubre');
    assert.equal(
        weekLabel('2026-09-28', '2026-10-04'),
        'Semana del 28 de septiembre al 4 de octubre',
    );
    assert.equal(weekLabel(undefined, '2026-10-15'), null);
    assert.equal(weekLabel('no es fecha', '2026-10-15'), null);
});

test('a day name is read from the date itself, not shifted by the timezone', () => {
    assert.equal(dayName('2026-10-15'), 'Jueves');
    assert.equal(parseIsoDay('2026-10-09').day, 9);
    assert.equal(dayName('basura'), '');
});

test('a trend is signed and coloured only where a change is good or bad', () => {
    assert.deepEqual(trendText(9, ' pts', { invertColors: true }),
        { tone: 'up', text: '+9 pts vs. anterior' });
    assert.deepEqual(trendText(-2.1, ' %'), { tone: 'none', text: '−2,1 % vs. anterior' });
    assert.deepEqual(trendText(-3, ' pts', { invertColors: true }),
        { tone: 'down', text: '−3 pts vs. anterior' });
    assert.deepEqual(trendText(0, ' °C'), { tone: 'flat', text: 'igual que la anterior' });
    assert.deepEqual(trendText(null, ' °C'), { tone: 'none', text: 'sin semana anterior' });
});

test('a hex token becomes an rgba with the given alpha; anything else is passed through', () => {
    assert.equal(withAlpha('#8FC79A', 0.13), 'rgba(143, 199, 154, 0.13)');
    assert.equal(withAlpha('rgb(1, 2, 3)', 0.5), 'rgb(1, 2, 3)');
});
