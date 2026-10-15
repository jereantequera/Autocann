/**
 * El estado de la barra: "Lazo caído" sólo cuando el lazo debería estar
 * corriendo y no está. Pausa, monitoreo y backend caído ya dicen algo cierto.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { loopIsDown, loopOverride, startsCollapsed } from '../../autocann/web/static/js/sidebar.js';

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
