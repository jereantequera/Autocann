/**
 * What the Riego pieces share: the last payloads the server sent, and the
 * helpers every piece uses.
 *
 * One module, so the form, the log and the plan read the same copy instead of
 * each keeping their own.
 */

import { config } from './state.js';

export const feedState = {
    /** Last payload of /api/feeding/today, so the form can be reset to the plan. */
    today: null,
    /** Last payload of /api/feed-plan. */
    plan: null,
    /** The irrigation log rows. */
    history: [],
};

export function el(id) {
    return document.getElementById(id);
}

/**
 * Tell the calendar and the panel tile what the log says.
 *
 * They render the same rows this module fetched instead of asking for them
 * again, so three places cannot end up showing three different logs.
 */
export function announce() {
    document.dispatchEvent(new CustomEvent('feeding:updated', {
        detail: { today: feedState.today, irrigations: feedState.history },
    }));
}

export function stageName(stage) {
    return config.stageNames[stage] || stage || '--';
}
