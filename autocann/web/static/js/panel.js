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
// The watering tile
// ---------------------------------------------------------------------------
// Watering is the one thing on this screen somebody has to do rather than
// watch, so the panel carries how long it has been and what the chart asks for
// today - and sends them to Riego to actually do it.

function renderFeedTile({ irrigations, today }) {
    const value = document.getElementById('panel-feed-value');
    const label = document.getElementById('panel-feed-label');
    const detail = document.getElementById('panel-feed-detail');
    const tile = document.getElementById('panel-feed');
    if (!value) return;

    const last = (irrigations || [])[0];
    if (!last) {
        value.textContent = '--';
        label.textContent = 'sin riegos registrados';
        if (detail) detail.textContent = 'Registrá el primero en Riego.';
        if (tile) tile.className = 'panel-feed';
        return;
    }

    const lastDate = new Date(`${last.date}T00:00:00`);
    const midnight = new Date();
    midnight.setHours(0, 0, 0, 0);
    const days = Math.round((midnight - lastDate) / 86400000);

    value.textContent = days <= 0 ? 'hoy' : days;
    label.textContent = days <= 0 ? 'regado' : `${days === 1 ? 'día' : 'días'} desde el último riego`;

    // Four days is where the badge in the log already turns stale; the same
    // threshold here so the two screens cannot disagree.
    if (tile) tile.className = `panel-feed${days >= 4 ? ' is-stale' : ''}`;

    if (!detail) return;
    if (!today) {
        detail.textContent = `Último: ${last.date} · ${last.litres} L`;
    } else if (today.flush) {
        detail.textContent = 'Plan de hoy: semana de lavado, agua sola.';
    } else if (today.items && today.items.length) {
        detail.textContent = `Plan de hoy: ${today.items.length} `
            + `${today.items.length === 1 ? 'producto' : 'productos'}`
            + ` · ${last.litres} L el ${last.date}`;
    } else {
        detail.textContent = `Sin plan cargado para esta semana · ${last.litres} L el ${last.date}`;
    }
}

function wirePanel() {
    document.addEventListener('feeding:updated', event => renderFeedTile(event.detail || {}));
}

export { PANEL_HOURS, fetchPanelHistory, wirePanel };
