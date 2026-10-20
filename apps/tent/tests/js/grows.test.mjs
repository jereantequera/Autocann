/**
 * Tests for the pure helpers behind the Cultivo view (grow-model.js).
 *
 * Run with: node --test tests/js/*.test.mjs
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    cultivoKicker, durationLabel, formatLongDate, formatShortDate, growDays, growLine, nextStage,
    profileLabel, stageBarModel, stageWeights, vpdLabel,
} from '../../autocann/web/static/js/grow-model.js';

const ORDER = ['seedling', 'early_veg', 'late_veg', 'flowering', 'dry'];
const NAMES = {
    seedling: 'Plántula',
    early_veg: 'Vegetativo temprano',
    late_veg: 'Vegetativo tardío',
    flowering: 'Floración',
    dry: 'Secado',
};

test('nextStage follows the cycle and stops at the last stage', () => {
    assert.equal(nextStage(ORDER, 'early_veg'), 'late_veg');
    assert.equal(nextStage(ORDER, 'seedling'), 'early_veg');
    assert.equal(nextStage(ORDER, 'dry'), null, 'nothing comes after drying');
    assert.equal(nextStage(ORDER, 'unknown'), null, 'an unknown stage has no next one');
});

test('stageWeights are the expected days when every stage has them', () => {
    const expected = { seedling: 14, early_veg: 21, late_veg: 28, flowering: 56, dry: 10 };
    assert.deepEqual(stageWeights(ORDER, expected), [14, 21, 28, 56, 10]);
});

test('stageWeights are equal columns until every length is known', () => {
    // A bar drawn from a partial guess would read as a forecast nobody made.
    assert.deepEqual(stageWeights(ORDER, { early_veg: 21 }), [1, 1, 1, 1, 1]);
    assert.deepEqual(stageWeights(ORDER, null), [1, 1, 1, 1, 1]);
});

test('stageBarModel marks done, now and next, and fills the current stage', () => {
    const bar = stageBarModel({
        order: ORDER,
        current: 'early_veg',
        dayInStage: 1,
        expected: { seedling: 14, early_veg: 21, late_veg: 28, flowering: 56, dry: 10 },
        timeline: [{ stage: 'early_veg', days: 1 }],
        names: NAMES,
    });
    assert.deepEqual(bar.map(cell => cell.state), ['done', 'now', 'next', 'next', 'next']);
    assert.equal(bar[0].meta, 'salteada', 'seedling was never entered');
    assert.equal(bar[1].meta, 'día 1 de ~21');
    assert.ok(Math.abs(bar[1].fill - 100 / 21) < 1e-9);
    assert.equal(bar[2].meta, '~28 d');
    assert.equal(bar[2].name, 'Vegetativo tardío');
});

test('stageBarModel reports real days for a stage the grow went through', () => {
    const bar = stageBarModel({
        order: ORDER,
        current: 'flowering',
        dayInStage: 80,
        expected: { flowering: 56 },
        timeline: [{ stage: 'seedling', days: 15 }, { stage: 'flowering', days: 80 }],
        names: NAMES,
    });
    assert.equal(bar[0].meta, '15 d');
    assert.equal(bar[0].fill, 100);
    assert.equal(bar[3].fill, 100, 'a stage past its length fills the bar and stops there');
});

test('stageBarModel with no active grow leaves every cell as a next stage', () => {
    const bar = stageBarModel({
        order: ORDER, current: null, dayInStage: null, expected: null, timeline: [], names: {},
    });
    assert.ok(bar.every(cell => cell.state === 'next' && cell.fill === 0));
    assert.equal(bar[0].name, 'seedling', 'falls back to the stage key without a name');
});

test('formatShortDate and formatLongDate read the calendar day, not UTC', () => {
    // new Date('2026-10-15') is UTC midnight, which is the 14th in Argentina.
    assert.equal(formatShortDate('2026-10-15 12:36:58'), '15/10/2026');
    assert.equal(formatShortDate('2026-10-15'), '15/10/2026');
    assert.equal(formatLongDate('2026-10-15 00:00:00'), '15 de octubre');
    assert.equal(formatShortDate(null), '--');
    assert.equal(formatLongDate('not a date'), '--');
});

test('growDays counts the first day as day 1 and stops at the end date', () => {
    const now = new Date(2026, 9, 15, 18, 0, 0);
    assert.equal(growDays('2026-10-15 12:36:58', null, now), 1);
    assert.equal(growDays('2026-10-01', null, now), 15);
    assert.equal(growDays('2026-10-01', '2026-10-10', now), 10);
    assert.equal(growDays(null, null, now), null);
});

test('durationLabel is singular for one day', () => {
    assert.equal(durationLabel(1), '1 día');
    assert.equal(durationLabel(21), '21 días');
    assert.equal(durationLabel(null), '--');
});

test('vpdLabel uses the decimal comma and drops a missing band', () => {
    assert.equal(vpdLabel({ min: 0.6, max: 1 }), '0,6 – 1,0 kPa');
    assert.equal(vpdLabel(undefined), null);
});

test('growLine joins the parts the server knows and drops the rest', () => {
    assert.equal(
        growLine({ stageName: 'Vegetativo temprano', day: 1, expected: 21, vpd: { min: 0.6, max: 1 } }),
        'Vegetativo temprano · día 1 de ~21 · VPD objetivo 0,6 – 1,0 kPa',
    );
    assert.equal(growLine({ stageName: 'Secado', day: null, expected: null, vpd: null }), 'Secado');
    assert.equal(growLine({ stageName: 'Floración', day: 3, expected: null, vpd: null }), 'Floración · día 3');
});

test('nextStage output is accepted by stageBarModel without surprises', () => {
    const current = 'late_veg';
    const bar = stageBarModel({
        order: ORDER, current, dayInStage: 2, expected: null, timeline: [], names: NAMES,
    });
    assert.equal(bar.find(cell => cell.state === 'now').stage, current);
    assert.equal(nextStage(ORDER, current), 'flowering');
});

test('cultivoKicker names the profile after the start date', () => {
    assert.equal(
        cultivoKicker({ startDate: '2026-10-12 08:00:00', profileName: 'Cannabis fotoperiódico' }),
        'Cultivo activo · desde el 12 de octubre · Cannabis fotoperiódico',
    );
    assert.equal(
        cultivoKicker({ startDate: '2026-10-12', profileName: 'Mi receta' }),
        'Cultivo activo · desde el 12 de octubre · Mi receta',
    );
});

test('cultivoKicker says Perfil eliminado when the profile is gone', () => {
    assert.equal(
        cultivoKicker({ startDate: '2026-10-12', profileName: null }),
        'Cultivo activo · desde el 12 de octubre · Perfil eliminado',
    );
    assert.equal(profileLabel(''), 'Perfil eliminado');
    assert.equal(profileLabel(undefined), 'Perfil eliminado');
});
