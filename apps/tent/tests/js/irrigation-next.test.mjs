/**
 * Tests for the pure helpers in static/js/irrigation-next.js: the words the
 * "Próximo riego" card shows, and the check on the interval the grower types.
 *
 * Run with: node --test tests/js/*.test.mjs
 *
 * Dates are fixed ISO days, not "today": 2026-10-17 is a Saturday, so the
 * weekday words below are the ones the calendar gives for these days.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    nextIrrigationCopy, parseIntervalInput,
} from '../../autocann/web/static/js/irrigation-next.js';

const configured = (next, extra = {}) => ({
    configured: true,
    grow_id: 1,
    interval_days: 2,
    last_irrigation_date: null,
    next,
    ...extra,
});

test('an unconfigured grow is asked for an interval, not told a date', () => {
    const copy = nextIrrigationCopy({ configured: false, grow_id: 1 });
    assert.equal(copy.headline, 'Configurá cada cuántos días regás');
    assert.equal(copy.tone, 'unset');
});

test('a watering due today says so', () => {
    const copy = nextIrrigationCopy(configured({
        due_date: '2026-10-17', days_until: 0, overdue_days: 0, state: 'today',
    }));
    assert.equal(copy.headline, 'Toca regar hoy');
    assert.equal(copy.tone, 'today');
});

test('a grow with no watering yet is due today, and says why', () => {
    const copy = nextIrrigationCopy(configured({
        due_date: '2026-10-17', days_until: 0, overdue_days: 0, state: 'no_history',
    }));
    assert.equal(copy.headline, 'Toca regar hoy');
    assert.match(copy.detail, /Todavía no hay riegos/);
});

test('an upcoming watering names the weekday and the day, and counts the days', () => {
    const copy = nextIrrigationCopy(configured({
        due_date: '2026-10-22', days_until: 5, overdue_days: 0, state: 'upcoming',
    }));
    assert.equal(copy.headline, 'Próximo riego: jueves 22 (en 5 días)');
    assert.equal(copy.tone, '');
});

test('one day ahead is singular', () => {
    const copy = nextIrrigationCopy(configured({
        due_date: '2026-10-18', days_until: 1, overdue_days: 0, state: 'upcoming',
    }));
    assert.equal(copy.headline, 'Próximo riego: domingo 18 (en 1 día)');
});

test('overdue by one day says so, and when it was due', () => {
    const copy = nextIrrigationCopy(configured({
        due_date: '2026-10-20', days_until: -1, overdue_days: 1, state: 'overdue',
    }));
    assert.equal(copy.headline, 'Atrasado 1 día — tocaba el martes');
    assert.equal(copy.tone, 'overdue');
});

test('overdue by three days is plural', () => {
    const copy = nextIrrigationCopy(configured({
        due_date: '2026-10-20', days_until: -3, overdue_days: 3, state: 'overdue',
    }));
    assert.equal(copy.headline, 'Atrasado 3 días — tocaba el martes');
});

test('the last watering is shown as a detail line when there is one', () => {
    // A date from a fixed "today" would make this test drift; the only thing
    // asserted is that the line is there and carries the label.
    const payload = configured({
        due_date: '2026-10-22', days_until: 5, overdue_days: 0, state: 'upcoming',
    }, { last_irrigation_date: '2020-01-01' });
    assert.match(nextIrrigationCopy(payload).detail, /^Último riego: hace \d+ días$/);
});

test('a missing or failed answer shows a dash, not a guess', () => {
    assert.equal(nextIrrigationCopy(null).headline, '--');
    assert.equal(nextIrrigationCopy({ error: 'No active grow found' }).headline, '--');
});

test('an empty interval means none, and whole numbers from 1 to 30 are accepted', () => {
    assert.deepEqual(parseIntervalInput(''), { ok: true, days: null });
    assert.deepEqual(parseIntervalInput('   '), { ok: true, days: null });
    assert.deepEqual(parseIntervalInput('2'), { ok: true, days: 2 });
    assert.deepEqual(parseIntervalInput(' 30 '), { ok: true, days: 30 });
    assert.deepEqual(parseIntervalInput('1'), { ok: true, days: 1 });
});

test('anything else is refused before it reaches the server', () => {
    for (const bad of ['0', '31', '-1', '2.5', '1e1', 'dos', '2 días']) {
        const result = parseIntervalInput(bad);
        assert.equal(result.ok, false, bad);
        assert.match(result.error, /de 1 a 30/);
    }
});
