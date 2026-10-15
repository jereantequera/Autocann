/**
 * Sistema: qué filas de calibración cambiaron, y qué ids no tienen sensor.
 *
 * Guardar todas las filas dice "guardado" sobre valores que nadie tocó; y una
 * corrección guardada contra un id que nada postea no corrige nada, pero se ve
 * igual que una que funciona.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { calibrationIds, changedCalibrations, sensorLabel } from '../../autocann/web/static/js/system.js';

const stored = {
    a: { temperature_offset: 0.5, humidity_offset: 0, notes: 'termómetro' },
    b: { temperature_offset: 0, humidity_offset: 0, notes: null },
};

test('only the rows that differ from the server are sent', () => {
    const rows = [
        { sensorId: 'a', temperature: 0.5, humidity: 0, notes: 'termómetro' },
        { sensorId: 'b', temperature: 0, humidity: 0, notes: null },
    ];
    assert.deepEqual(changedCalibrations(rows, stored), []);

    const edited = [{ sensorId: 'b', temperature: -0.2, humidity: 0, notes: null }];
    assert.deepEqual(changedCalibrations(edited, stored).map(row => row.sensorId), ['b']);
});

test('a missing field is zero, so "0" against nothing is not a change', () => {
    const rows = [{ sensorId: 'c', temperature: 0, humidity: 0, notes: null }];
    assert.deepEqual(changedCalibrations(rows, {}), []);
});

test('a note edited alone is a change', () => {
    const rows = [{ sensorId: 'a', temperature: 0.5, humidity: 0, notes: 'otra nota' }];
    assert.equal(changedCalibrations(rows, stored).length, 1);
});

test('an offset against an id nothing posts is an orphan, listed after the live ones', () => {
    const { live, orphans } = calibrationIds(['b', 'a'], { a: {}, b: {}, ghost: {}, old: {} });
    assert.deepEqual(live, ['a', 'b']);
    assert.deepEqual(orphans, ['ghost', 'old']);
});

test('a sensor id is labelled for a person: a letter says which sensor it is', () => {
    assert.equal(sensorLabel('a'), 'Sensor A');
    assert.equal(sensorLabel('dht22_outdoor'), 'dht22_outdoor');
});
