/**
 * Tests for the pure helpers of the Fotoperíodo control (static/js/photoperiod.js).
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    hourLabel, lightsHours, photoperiodProblem, photoperiodText,
} from '../../autocann/web/static/js/photoperiod.js';

test('an hour is written with its leading zero', () => {
    assert.equal(hourLabel(6), '06:00');
    assert.equal(hourLabel(0), '00:00');
    assert.equal(hourLabel(23), '23:00');
});

test('the lights are on for the hours between on and off, across midnight too', () => {
    assert.equal(lightsHours(6, 0), 18);
    assert.equal(lightsHours(18, 12), 18);
    assert.equal(lightsHours(6, 12), 6);
});

test('the sentence says the window and its length', () => {
    assert.equal(photoperiodText(6, 0), 'Luces de 06:00 a 00:00: 18 h.');
    assert.equal(photoperiodText(18, 12), 'Luces de 18:00 a 12:00: 18 h.');
});

test('equal hours are not a window and are said so', () => {
    assert.match(photoperiodProblem(7, 7), /misma hora/);
    assert.equal(photoperiodProblem(6, 0), null);
});
