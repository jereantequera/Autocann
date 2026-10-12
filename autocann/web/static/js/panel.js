/**
 * The 24/7 panel's own two pieces.
 *
 * Both exist because the panel must not depend on anything selected somewhere
 * else: its history is fixed at 24 hours rather than reading the range selector
 * that now lives in Estadísticas, and the watering tile reads the log the
 * feeding module already fetched.
 */

import { apiGet } from './api.js';
import { updatePanelChart } from './charts.js';

/** Fixed, deliberately: a wall screen has nobody to pick a range on it. */
const PANEL_HOURS = 24;

function fetchPanelHistory() {
    apiGet(`/api/history/aggregated?days=1&interval=hourly`, { key: 'panel-history' })
        .then(data => {
            if (!data || !data.data) return;
            updatePanelChart(data.data);
            const note = document.getElementById('panel-chart-note');
            if (note) {
                note.textContent = data.data.length
                    ? `${data.data.length} h de promedios horarios`
                    : 'sin datos en las últimas 24 h';
            }
        });
}

// ---------------------------------------------------------------------------
// The watering CTA
// ---------------------------------------------------------------------------
// The panel used to carry a whole tile for this: days since the last watering,
// today's doses, a link. All of it was a worse copy of the Riego tab, on the
// one screen that exists to be read rather than used. What is left is the part
// that is actually a prompt to act - that it has been a while - hung on the
// button in the header, and only when it has.

/** Days before an unwatered tent is worth saying out loud. The log's own badge
 *  turns stale at the same number, so the two screens cannot disagree. */
const STALE_AFTER_DAYS = 4;

function renderFeedBadge({ irrigations }) {
    const badge = document.getElementById('feed-cta-badge');
    if (!badge) return;

    const last = (irrigations || [])[0];
    if (!last) {
        // No log at all is not an alarm: it is a tent nobody has watered yet,
        // and the button already says what to do about that.
        badge.hidden = true;
        return;
    }

    const lastDate = new Date(`${last.date}T00:00:00`);
    const midnight = new Date();
    midnight.setHours(0, 0, 0, 0);
    const days = Math.round((midnight - lastDate) / 86400000);

    badge.hidden = days < STALE_AFTER_DAYS;
    badge.textContent = `${days} d`;
    badge.title = `Último riego: ${last.date}`;
}

function wirePanel() {
    document.addEventListener('feeding:updated', event => renderFeedBadge(event.detail || {}));
}

export { PANEL_HOURS, fetchPanelHistory, wirePanel };
