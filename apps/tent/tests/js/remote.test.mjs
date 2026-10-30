/**
 * Tests for where a virtual-remote press stands (static/js/remote.js).
 *
 * Run with: node --test tests/js/*.test.mjs
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { pressOutcome } from '../../autocann/web/static/js/remote.js';

test('a press is sent once the node has acknowledged it', () => {
    assert.equal(pressOutcome({ pending: null, last: { id: 4 } }, 4), 'sent');
});

test('a press still pending is waiting', () => {
    assert.equal(pressOutcome({ pending: { id: 4 }, last: { id: 3 } }, 4), 'waiting');
});

test('a press that is neither pending nor sent was lost', () => {
    assert.equal(pressOutcome({ pending: null, last: { id: 3 } }, 4), 'lost');
    assert.equal(pressOutcome({ pending: null, last: null }, 4), 'lost');
});
