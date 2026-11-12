/**
 * Tests for the gap helpers behind the history charts
 * (static/js/chartgaps.js): the typical sample spacing, where a line must
 * break, and how a plan picks values out of a column.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { applyPlan, expectedSpacing, gapPlan } from '../../autocann/web/static/js/chartgaps.js';

const FIVE_MIN = 300;

/** Samples every `step` seconds, for `count` slots, leaving out the indexes in `skip`. */
function samples(count, step = FIVE_MIN, skip = []) {
    const stamps = [];
    for (let i = 0; i < count; i++) {
        if (!skip.includes(i)) stamps.push(i * step);
    }
    return stamps;
}

test('expected spacing is the median step, so one long hole does not move it', () => {
    const stamps = samples(20, FIVE_MIN, [5, 6, 7, 8, 9, 10]);
    assert.equal(expectedSpacing(stamps), FIVE_MIN);
});

test('expected spacing is 0 with fewer than two samples', () => {
    assert.equal(expectedSpacing([]), 0);
    assert.equal(expectedSpacing([42]), 0);
});

test('expected spacing averages the two middle steps of an even count', () => {
    // Steps 100 and 300: the middle of two is their mean.
    assert.equal(expectedSpacing([0, 100, 400]), 200);
});

test('a regular series has no gap', () => {
    const stamps = samples(50);
    assert.deepEqual(gapPlan(stamps), stamps.map((_, i) => i));
});

test('a hole wider than three steps gets one null row between its samples', () => {
    // Samples 10..35 are missing: the step from 9 to 36 is 27 steps long.
    const stamps = samples(60, FIVE_MIN, Array.from({ length: 26 }, (_, k) => k + 10));
    const plan = gapPlan(stamps);
    const at = plan.indexOf(null);
    assert.ok(at > 0, 'a null row is planned');
    assert.equal(plan.filter(i => i === null).length, 1);
    assert.equal(plan[at - 1], stamps.indexOf(9 * FIVE_MIN));
    assert.equal(plan[at + 1], stamps.indexOf(36 * FIVE_MIN));
});

test('a step of exactly three steps is not a gap, just over it is', () => {
    // Four 300 s steps and then a 900 s step: the typical step is 300.
    assert.equal(gapPlan([0, 300, 600, 900, 1200, 2100]).includes(null), false);
    assert.equal(gapPlan([0, 300, 600, 900, 1200, 2101]).includes(null), true);
});

test('the threshold can be set by the caller', () => {
    // A 700 s step is a gap at factor 2 (over 600 s) but not at factor 3.
    assert.equal(gapPlan([0, 300, 600, 1300], 2).includes(null), true);
    assert.equal(gapPlan([0, 300, 600, 1300]).includes(null), false);
});

test('hourly aggregated buckets are judged against the hourly spacing', () => {
    // Hours 12 to 14 are missing: the step from 11 to 15 is four hours long.
    const plan = gapPlan(samples(24, 3600, [12, 13, 14]));
    assert.equal(plan.filter(i => i === null).length, 1);
    assert.equal(gapPlan(samples(24, 3600)).includes(null), false);
});

test('a plan with no samples or one sample has nothing to break', () => {
    assert.deepEqual(gapPlan([]), []);
    assert.deepEqual(gapPlan([7]), [0]);
});

test('applyPlan picks values through the plan and leaves the gaps empty', () => {
    const plan = [0, 1, null, 2];
    assert.deepEqual(applyPlan(plan, ['a', 'b', 'c']), ['a', 'b', null, 'c']);
    assert.deepEqual(applyPlan(plan, [1, 2, 3], NaN).map(Number.isNaN), [false, false, true, false]);
});

test('applyPlan turns undefined values into null so the line still breaks', () => {
    assert.deepEqual(applyPlan([0, 1], [5, undefined]), [5, null]);
});
