/**
 * Tests for the tent name check in Sistema (tent-name.js): what the form
 * accepts before it sends anything.
 *
 * Run with: node --test tests/js/*.test.mjs
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { TENT_NAME_MAX, checkTentName } from '../../autocann/web/static/js/tent-name.js';

test('the name is trimmed and kept', () => {
    assert.deepEqual(checkTentName('  Carpa del balcón  '), { ok: true, name: 'Carpa del balcón' });
});

test('a blank name is refused with a sentence, not a code', () => {
    assert.equal(checkTentName('   ').ok, false);
    assert.equal(checkTentName('').error, 'Ponele un nombre a la carpa.');
    assert.equal(checkTentName(undefined).ok, false);
});

test('the limit is 60 characters, counted the way the server counts them', () => {
    assert.equal(TENT_NAME_MAX, 60);
    assert.equal(checkTentName('a'.repeat(60)).ok, true);
    assert.equal(checkTentName('a'.repeat(61)).ok, false);
    // One emoji is one character to the server, even though JavaScript counts two units.
    assert.equal(checkTentName('🌿'.repeat(60)).ok, true);
    assert.equal(checkTentName('🌿'.repeat(61)).error, 'El nombre puede tener hasta 60 caracteres.');
});
