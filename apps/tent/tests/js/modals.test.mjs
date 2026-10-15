/**
 * Tests for the focus trap in static/js/modals.js.
 *
 * Lo que se prueba es la parte pura: dado qué es enfocable y dónde está el
 * foco, a dónde tiene que ir el Tab. Era el defecto del backlog #9 y de la
 * Fase 0 — con el modal abierto, tabular salía a los botones de atrás, que un
 * usuario de teclado o de lector de pantalla no puede ver que están tapados.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { nextFocus } from '../../autocann/web/static/js/modals.js';

// Plain objects: the helper only ever compares identity and looks at indices.
const field = { name: 'field' };
const cancel = { name: 'cancel' };
const confirm = { name: 'confirm' };
const items = [field, cancel, confirm];

test('tab in the middle of the dialog is left to the browser', () => {
    // Returning null rather than the next element: the native order is already
    // right there, and recomputing it is how a trap starts skipping fields.
    assert.equal(nextFocus(items, field, false), null);
    assert.equal(nextFocus(items, cancel, false), null);
    assert.equal(nextFocus(items, confirm, true), null);
});

test('tab off the last element wraps to the first instead of leaving', () => {
    assert.equal(nextFocus(items, confirm, false), field);
});

test('shift+tab off the first element wraps to the last', () => {
    // The direction that is usually forgotten. Fixing only the forward one
    // leaves a trap you can walk out of backwards.
    assert.equal(nextFocus(items, field, true), confirm);
});

test('focus that escaped the dialog is brought back', () => {
    // A click on the page behind, or a re-render that removed whatever had
    // focus: either way the next Tab must not continue from out there.
    const outside = { name: 'a button on the page behind' };
    assert.equal(nextFocus(items, outside, false), field);
    assert.equal(nextFocus(items, outside, true), field);
    assert.equal(nextFocus(items, null, false), field);
});

test('a dialog with nothing focusable asks for nothing', () => {
    for (const empty of [[], null, undefined]) {
        assert.equal(nextFocus(empty, field, false), null);
    }
});

test('a single focusable element keeps the focus on itself', () => {
    const only = [confirm];
    assert.equal(nextFocus(only, confirm, false), confirm);
    assert.equal(nextFocus(only, confirm, true), confirm);
});
