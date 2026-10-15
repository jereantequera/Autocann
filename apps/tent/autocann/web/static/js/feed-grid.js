/**
 * Pure helpers for the "Plan por semana" grid (feeding.js draws it).
 *
 * They take the column list the server sends (`{stage, week}` per column, in
 * chart order) and say how to group it. No DOM and no imports, so the tests
 * can load this file on its own.
 */

/**
 * Consecutive columns that share a stage, as the header needs them: one
 * group per stage, with the index of its first column and how many weeks it
 * spans. The stage label is drawn once over that span.
 */
export function stageGroups(columns) {
    const groups = [];
    columns.forEach((column, index) => {
        const last = groups[groups.length - 1];
        if (last && last.stage === column.stage) {
            last.span += 1;
        } else {
            groups.push({ stage: column.stage, start: index, span: 1 });
        }
    });
    return groups;
}

/**
 * The index of the column the grow is in right now, or -1 when there is none
 * (no active grow, or a stage the chart does not list). The server reports
 * week 0 when no stage is set, and weeks are 1-based, so a falsy week never
 * matches.
 */
export function currentColumnIndex(columns, stage, week) {
    if (!stage || !week) return -1;
    return columns.findIndex(column => column.stage === stage && column.week === Number(week));
}

/** Width of one week column in the grid; the CSS sets the same number. */
export const WEEK_COLUMN_PX = 56;

/**
 * Short names for stages whose column span is too narrow for the full name.
 * The grid is a table of fixed-width weeks, so a one-week stage cannot hold
 * "Plántula" at 13px; it gets "Plánt." instead.
 */
export const STAGE_ABBREVIATIONS = {
    seedling: 'Plánt.',
    early_veg: 'V. temp.',
    late_veg: 'V. tard.',
    flowering: 'Flor.',
    dry: 'Secado',
};

/** Rough advance of one 13px label character, and the header's left padding. */
const LABEL_CHAR_PX = 7.5;
const LABEL_PADDING_PX = 8;

/**
 * The text a stage header shows over `span` week columns. The full name when
 * it fits, the abbreviation when it does not, so a stage label is never cut
 * off. The estimate is deliberately a little generous on the long side.
 */
export function stageLabel(stage, span, fullName) {
    const room = span * WEEK_COLUMN_PX;
    const needed = fullName.length * LABEL_CHAR_PX + LABEL_PADDING_PX;
    if (needed <= room) return fullName;
    return STAGE_ABBREVIATIONS[stage] || fullName;
}
