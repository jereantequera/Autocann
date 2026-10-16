/**
 * Gap handling for the history charts (pure, no DOM).
 *
 * A stretch with no samples (the tent was off, the Pi rebooted) must not be
 * drawn as a straight line across the hole. The charts get a null point at
 * each gap instead, and they break the line there (spanGaps: false).
 */

/** A step wider than this many typical steps is a gap. */
export const GAP_FACTOR = 3;

/**
 * The usual distance between consecutive timestamps, in the same unit.
 *
 * The median, not the mean: one long hole must not make every other step
 * look normal. Returns 0 when there are fewer than two timestamps.
 */
export function expectedSpacing(timestamps) {
    const steps = [];
    for (let i = 1; i < timestamps.length; i++) {
        steps.push(timestamps[i] - timestamps[i - 1]);
    }
    if (steps.length === 0) return 0;
    steps.sort((a, b) => a - b);
    const middle = steps.length >> 1;
    return steps.length % 2 ? steps[middle] : (steps[middle - 1] + steps[middle]) / 2;
}

/**
 * The rows to plot, in order: each entry is the index of a sample, or null
 * where a gap sits between two samples.
 *
 * @param {Array<number>} timestamps  sorted, in seconds
 * @param {number} factor  how many typical steps a step must exceed to be a gap
 */
export function gapPlan(timestamps, factor = GAP_FACTOR) {
    const spacing = expectedSpacing(timestamps);
    const plan = [];
    timestamps.forEach((stamp, i) => {
        if (i > 0 && spacing > 0 && stamp - timestamps[i - 1] > factor * spacing) {
            plan.push(null);
        }
        plan.push(i);
    });
    return plan;
}

/**
 * Pick one column of values through a plan: `gap` where the plan has a gap.
 */
export function applyPlan(plan, values, gap = null) {
    return plan.map(i => (i === null ? gap : values[i] ?? null));
}
