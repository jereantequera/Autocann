/**
 * Tests for the Plan por semana grid's helpers (static/js/feed-grid.js).
 *
 * The header groups the server's columns by stage, and the grid marks the
 * column the grow is in. Both are plain arithmetic over the column list, so
 * they are pinned here rather than through the DOM.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    currentColumnIndex, stageGroups, stageLabel,
} from '../../autocann/web/static/js/feed-grid.js';

const columns = [
    { stage: 'seedling', week: 1 },
    { stage: 'seedling', week: 2 },
    { stage: 'early_veg', week: 1 },
    { stage: 'early_veg', week: 2 },
    { stage: 'early_veg', week: 3 },
    { stage: 'flowering', week: 1 },
];

test('stage groups span their weeks and start at their first column', () => {
    assert.deepEqual(stageGroups(columns), [
        { stage: 'seedling', start: 0, span: 2 },
        { stage: 'early_veg', start: 2, span: 3 },
        { stage: 'flowering', start: 5, span: 1 },
    ]);
});

test('a stage that appears twice in a row is still one group', () => {
    // Two groups for the same stage would draw a second label and a second
    // hairline in the middle of the stage.
    assert.equal(stageGroups([{ stage: 'dry', week: 1 }, { stage: 'dry', week: 2 }]).length, 1);
});

test('no columns, no groups', () => {
    assert.deepEqual(stageGroups([]), []);
});

test('the current column is the one matching the grow stage and week', () => {
    assert.equal(currentColumnIndex(columns, 'early_veg', 2), 3);
    assert.equal(currentColumnIndex(columns, 'flowering', 1), 5);
});

test('no current column without a stage, a week, or a listed stage', () => {
    // Week 0 is what the server sends when no stage is set; weeks are 1-based.
    assert.equal(currentColumnIndex(columns, null, 0), -1);
    assert.equal(currentColumnIndex(columns, 'seedling', 0), -1);
    assert.equal(currentColumnIndex(columns, 'dry', 1), -1);
    // A week past the end of its stage is not in the chart either.
    assert.equal(currentColumnIndex(columns, 'seedling', 3), -1);
});

test('the week may arrive as a string and still match', () => {
    assert.equal(currentColumnIndex(columns, 'early_veg', '3'), 4);
});

test('a stage label is the full name when the stage is wide enough for it', () => {
    // Two weeks is 112px: "Floración" (9 characters) fits, "Vegetativo Temprano" does not.
    assert.equal(stageLabel('flowering', 2, 'Floración'), 'Floración');
    assert.equal(stageLabel('early_veg', 2, 'Vegetativo Temprano'), 'V. temp.');
    assert.equal(stageLabel('early_veg', 3, 'Vegetativo Temprano'), 'Vegetativo Temprano');
});

test('a one-week stage gets its abbreviation instead of a clipped name', () => {
    assert.equal(stageLabel('seedling', 1, 'Plántula'), 'Plánt.');
    assert.equal(stageLabel('dry', 1, 'Secado'), 'Secado');
});
