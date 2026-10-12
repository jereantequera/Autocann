/**
 * Tests for the pure helpers in static/js/util.js.
 *
 * Run with: node --test tests/js/
 * The Python suite also runs these via tests/test_frontend.py, and skips when
 * node is not installed — the Raspberry does not need a JS toolchain.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    escapeHtml, fmtNum, fmtSigned, formatAge, formatDate, formatDuration, getScoreClass,
    getScoreLabel, parseLocalDatetime,
} from '../../autocann/web/static/js/util.js';

test('escapeHtml neutralises markup that reaches innerHTML', () => {
    // A grow name is written by whoever can reach the API, and the grow list
    // renders it with innerHTML. This is the fix for that XSS.
    assert.equal(
        escapeHtml('<img src=x onerror="alert(1)">'),
        '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;',
    );
    assert.equal(escapeHtml("it's & <b>"), 'it&#39;s &amp; &lt;b&gt;');
});

test('escapeHtml renders nullish as empty, not as the words null/undefined', () => {
    assert.equal(escapeHtml(null), '');
    assert.equal(escapeHtml(undefined), '');
    assert.equal(escapeHtml(0), '0');
});

test('fmtNum keeps a real zero instead of showing a dash', () => {
    // 0 °C of leaf temperature and 0% of target humidity are valid readings;
    // `x ? fmt(x) : '--'` used to hide both.
    assert.equal(fmtNum(0), '0.0');
    assert.equal(fmtNum(0, 2), '0.00');
    assert.equal(fmtNum(24.567, 1), '24.6');
});

test('fmtNum falls back for anything that is not a finite number', () => {
    for (const value of [null, undefined, NaN, Infinity, 'abc', {}]) {
        assert.equal(fmtNum(value), '--', `para ${String(value)}`);
    }
    assert.equal(fmtNum(null, 1, 's/d'), 's/d');
});

test('parseLocalDatetime accepts the API format Safari rejects', () => {
    // `new Date("2026-01-16 12:00:00")` is not a format the constructor must
    // accept, and Safari returns Invalid Date for it.
    const d = parseLocalDatetime('2026-01-16 12:30:45');
    assert.ok(!isNaN(d.getTime()));
    assert.equal(d.getFullYear(), 2026);
    assert.equal(d.getMonth(), 0);
    assert.equal(d.getDate(), 16);
    assert.equal(d.getHours(), 12);
    assert.equal(d.getMinutes(), 30);
});

test('parseLocalDatetime returns an invalid date for junk rather than throwing', () => {
    for (const value of [null, undefined, 42, 'no es una fecha']) {
        assert.ok(isNaN(parseLocalDatetime(value).getTime()), `para ${String(value)}`);
    }
});

test('formatDate granularity follows the selected view', () => {
    const ts = '2026-01-16 09:05:00';
    assert.equal(formatDate(ts, { hours: 6, interval: 'raw' }), '09:05');
    assert.equal(formatDate(ts, { hours: null, interval: 'daily' }), '16/01');
    assert.equal(formatDate(ts, { hours: null, interval: 'hourly' }), '16/01 09:05');
});

test('formatDate is pure: no shared state, so no view means the default', () => {
    assert.equal(formatDate('2026-01-16 09:05:00'), '16/01 09:05');
});

test('formatDate echoes an unparseable value instead of printing Invalid Date', () => {
    assert.equal(formatDate('qué fecha'), 'qué fecha');
    assert.equal(formatDate(null), '--');
});

test('score bands are contiguous and cover the whole 0-100 range', () => {
    const bands = [];
    for (let s = 0; s <= 100; s++) bands.push(getScoreClass(s));
    assert.ok(bands.every(Boolean), 'todo score debe tener una clase');
    assert.equal(getScoreClass(100), getScoreClass(95));
    assert.notEqual(getScoreClass(0), getScoreClass(100));
    assert.ok(getScoreLabel(90).length > 0);
    assert.ok(getScoreLabel(10).length > 0);
});

test('formatAge speaks in units a grower reads at a glance', () => {
    assert.equal(formatAge(4), 'hace 4 s');
    assert.equal(formatAge(59), 'hace 59 s');
    assert.equal(formatAge(60), 'hace 1 min');
    assert.equal(formatAge(3600), 'hace 1 h 0 min');
    assert.equal(formatAge(3 * 86400), 'hace 3 d');
});

test('formatAge never renders a negative age', () => {
    // The Raspberry has no real-time clock, so its timestamps can land ahead of
    // the browser's. "hace -3 min" is how that used to show up.
    assert.equal(formatAge(0), 'ahora');
    assert.equal(formatAge(-120), 'ahora');
});

test('formatAge says there is no reading rather than printing junk', () => {
    for (const value of [null, undefined, NaN, Infinity, 'viejo']) {
        assert.equal(formatAge(value), 'sin datos', `para ${String(value)}`);
    }
});

test('fmtSigned keeps the sign, which is the whole point of a difference', () => {
    assert.equal(fmtSigned(0.4), '+0.4');
    assert.equal(fmtSigned(-0.4), '-0.4');
    assert.equal(fmtSigned(0), '+0.0');
    assert.equal(fmtSigned(-1.234, 2), '-1.23');
    assert.equal(fmtSigned(null), '--');
});

test('formatDuration reads as time left, not as a number of seconds', () => {
    assert.equal(formatDuration(30), '30 s');
    assert.equal(formatDuration(90), '1 min');
    assert.equal(formatDuration(8 * 3600), '8 h 0 min');
    assert.equal(formatDuration(3 * 3600 + 25 * 60), '3 h 25 min');
});

test('formatDuration shows a dash rather than a countdown already past', () => {
    for (const value of [0, -5, null, undefined, NaN, 'pronto']) {
        assert.equal(formatDuration(value), '--', `para ${String(value)}`);
    }
});
