/**
 * El estado de la barra: "Lazo caído" sólo cuando el lazo debería estar
 * corriendo y no está. Pausa, monitoreo y backend caído ya dicen algo cierto.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    loopIsDown, loopOverride, monogram, stateLabel, startsCollapsed, tentLabel,
} from '../../autocann/web/static/js/sidebar.js';

const down = { loop: { state: 'down', level: 'error' } };
const running = { loop: { state: 'running', level: 'ok' } };

test('a loop that is down says so in the status block', () => {
    assert.equal(loopIsDown(down), true);
    assert.equal(loopOverride({ health: down }), 'Lazo caído');
});

test('a running or paused loop is not overridden', () => {
    assert.equal(loopOverride({ health: running }), null);
    assert.equal(loopOverride({ health: { loop: { state: 'paused' } } }), null);
    assert.equal(loopOverride({ health: undefined }), null);
    assert.equal(loopIsDown({ error: 'boom', loop: { state: 'down' } }), false);
});

test('paused, monitoring and offline keep the words they already have', () => {
    assert.equal(loopOverride({ health: down, paused: true }), null);
    assert.equal(loopOverride({ health: down, monitoring: true }), null);
    assert.equal(loopOverride({ health: down, offline: true }), null);
});

test('the sidebar starts collapsed on a narrow screen unless the user chose', () => {
    assert.equal(startsCollapsed(null, true), true);
    assert.equal(startsCollapsed(null, false), false);
    assert.equal(startsCollapsed('expanded', true), false);
    assert.equal(startsCollapsed('collapsed', false), true);
});

test('the monogram is the first letter and the number of the grow', () => {
    assert.equal(monogram('Cultivo #1'), 'C1');
    assert.equal(monogram('Cultivo #12'), 'C12');
    assert.equal(monogram('  cultivo 3 '), 'C3');
});

test('a grow with no number gets its first two letters, in capitals', () => {
    assert.equal(monogram('Lechuga'), 'LE');
    assert.equal(monogram('x'), 'X');
    assert.equal(monogram('Ñandú'), 'ÑA');
});

test('a grow with no name at all has no monogram rather than a made-up one', () => {
    assert.equal(monogram(''), '');
    assert.equal(monogram(undefined), '');
    assert.equal(monogram('#7'), '7');
});

test('the tent label is the same words the expanded block shows', () => {
    assert.equal(
        tentLabel({ tent: 'Carpa del balcón', name: 'Cultivo #1', stage: 'Vegetativo temprano', day: 1, expected: 21 }),
        'Carpa del balcón · Cultivo #1 · Vegetativo temprano · día 1 de ~21',
    );
});

test('the tent label leaves out what is not known, and never says NaN', () => {
    assert.equal(tentLabel({ tent: 'Carpa principal', name: 'Cultivo #1', stage: 'Floración', day: 4 }), 'Carpa principal · Cultivo #1 · Floración · día 4');
    assert.equal(tentLabel({ tent: 'Carpa principal', name: 'Cultivo #1', stage: 'Floración', day: NaN }), 'Carpa principal · Cultivo #1 · Floración');
    assert.equal(tentLabel({ tent: 'Carpa principal' }), 'Carpa principal');
    assert.equal(tentLabel({}), '', 'with no tent name there is nothing to say');
});

test('the status button says its state and then its verb', () => {
    assert.equal(stateLabel('Lazo caído', 'Detener'), 'Lazo caído · Detener');
    assert.equal(stateLabel('En pausa', 'Reanudar'), 'En pausa · Reanudar');
    assert.equal(stateLabel('Sin conexión', ''), 'Sin conexión');
});
