/**
 * State shared between modules.
 *
 * A single mutable object rather than loose globals, so it is obvious what is
 * shared and who writes to it.
 */

export const state = {
    period: 1,
    interval: 'hourly',
    hours: null,
    stage: 'early_veg',
    /** Stage periods of the active grow, for the chart markers. */
    timeline: null,
};

/**
 * Stage configuration, fetched from /api/config at startup.
 *
 * It used to be a hardcoded copy of the ranges in vpd_math.py, which is how the
 * two drifted apart. The defaults below are only a fallback for the moment
 * before the first response arrives.
 */
export const config = {
    vpdRanges: {
        early_veg: { min: 0.6, max: 1.0 },
        late_veg: { min: 0.8, max: 1.2 },
        flowering: { min: 1.2, max: 1.5 },
        dry: { min: 0.8, max: 1.2 },
    },
    stageNames: {
        early_veg: 'Vegetativo Temprano',
        late_veg: 'Vegetativo Tardío',
        flowering: 'Floración',
        dry: 'Secado',
    },
};

export function applyConfig(payload) {
    if (!payload) return;
    if (payload.vpd_ranges) config.vpdRanges = payload.vpd_ranges;
    if (payload.stage_names) config.stageNames = payload.stage_names;
}
