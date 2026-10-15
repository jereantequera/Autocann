/**
 * Tests for the bitácora's grouping (static/js/logbook.js).
 *
 * The log is read as a diary: entries are grouped by day, the day is named
 * "Hoy" or "Ayer" when it is one of those, and the filter chips pick the kind.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    dayLabel, dotClass, filterLogbook, groupLogbook,
} from '../../autocann/web/static/js/logbook.js';

const NOW = new Date(2026, 9, 15, 12, 0); // Thursday, 15 Oct 2026, local

const items = [
    { kind: 'salida', text: 'Se prendió el humidificador', detail: '', datetime: '2026-10-15 15:38:00' },
    { kind: 'riego', text: 'Riego · 10 L', detail: 'Grow 10 ml', datetime: '2026-10-15 09:10:00' },
    { kind: 'salida', text: 'Se apagó el deshumidificador', detail: '', datetime: '2026-10-14 14:02:00' },
    { kind: 'cultivo', text: 'Empezó el Cultivo #1', detail: '', datetime: '2026-10-13 12:36:00' },
    { kind: 'sistema', text: 'Sin fecha', detail: '', datetime: 'no es una fecha' },
];

test('days are named Hoy, Ayer, and otherwise by weekday and date', () => {
    assert.equal(dayLabel(new Date(2026, 9, 15, 8), NOW), 'Hoy');
    assert.equal(dayLabel(new Date(2026, 9, 14, 23), NOW), 'Ayer');
    assert.equal(dayLabel(new Date(2026, 9, 13, 12), NOW), 'mar 13 oct');
});

test('entries are grouped by day in the order they came, and unreadable dates are dropped', () => {
    const groups = groupLogbook(items, 'todo', NOW);
    assert.deepEqual(groups.map(group => group.label), ['Hoy', 'Ayer', 'mar 13 oct']);
    assert.equal(groups[0].events.length, 2);
    assert.equal(groups[0].events[0].time, '15:38');
    // The entry with no date is not shown under a made-up day.
    assert.equal(groups.flatMap(group => group.events).length, 4);
});

test('a chip shows only its kind', () => {
    assert.deepEqual(filterLogbook(items, 'riegos').map(item => item.kind), ['riego']);
    assert.equal(filterLogbook(items, 'salidas').length, 2);
    assert.equal(filterLogbook(items, 'unknown').length, items.length, 'an unknown chip falls back to all');
});

test('the dot says what kind of entry it is, and an outlet turning on is the warm one', () => {
    assert.equal(dotClass({ kind: 'salida', text: 'Se prendió el humidificador' }), 'ac-dot--warn');
    assert.equal(dotClass({ kind: 'salida', text: 'Se apagó el deshumidificador' }), 'ac-dot--idle');
    assert.equal(dotClass({ kind: 'riego', text: '' }), 'ac-dot--info');
    assert.equal(dotClass({ kind: 'cultivo', text: '' }), 'ac-dot--ok');
    assert.equal(dotClass({ kind: 'sistema', text: '' }), 'ac-dot--idle');
});
