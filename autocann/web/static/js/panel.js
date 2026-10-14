/**
 * The 24/7 panel's own pieces.
 *
 * They exist because the panel must not depend on anything selected somewhere
 * else: its history is fixed at 24 hours rather than reading the range selector
 * that now lives in Estadísticas, and the watering tile reads the plan the
 * feeding module already fetched.
 */

import { apiGet } from './api.js';
import { updatePanelChart } from './charts.js';
import { config } from './state.js';
import { doseAmount, escapeHtml, fmtDose, lastWateringLabel } from './util.js';

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
// Riego de hoy
// ---------------------------------------------------------------------------
// One of the three questions the panel exists to answer, and the only one that
// used to need a second tab. It renders the payload the feeding module already
// has rather than fetching its own, so the tile and the Riego view cannot show
// two different plans - which is exactly how the previous version of this tile
// earned being deleted.

function el(id) {
    return document.getElementById(id);
}

function stageName(stage) {
    return config.stageNames[stage] || stage || '--';
}

/** What kind of week this is, in two words, for the section badge. */
function planBadge(today) {
    if (!today) return { text: '⏳', state: '' };
    if (today.flush) return { text: '💧 lavado', state: '' };

    const count = (today.items || []).length;
    if (!count) return { text: 'plan vacío', state: 'stale' };
    return { text: `${count} ${count === 1 ? 'producto' : 'productos'}`, state: 'ok' };
}

/**
 * One row per product: name, what to pour, and the dose it came from.
 *
 * The amount is the middle column and the only one set in the monospace,
 * because it is the number that gets measured into the jug; the per-litre dose
 * is kept beside it so a different volume can be worked out by hand without
 * opening the plan.
 */
function dosesHtml(today) {
    if (!today) return '<div class="loading">Cargando plan</div>';

    if (today.flush) {
        return `
            <div class="panel-feed-note">
                Semana de lavado: el plan no lleva nutrientes.
                Regá con agua sola y el pH corregido.
            </div>`;
    }
    if (!(today.items || []).length) {
        return `
            <div class="panel-feed-note">
                No hay nada cargado en el plan para esta semana.
                Completá la grilla en Riego → Plan.
            </div>`;
    }

    return today.items.map(item => `
        <div class="panel-feed-dose">
            <span class="panel-feed-dose-name">${escapeHtml(item.name)}</span>
            <span class="panel-feed-dose-total">${escapeHtml(doseAmount(item, today.litres))}</span>
            <span class="panel-feed-dose-rate">${escapeHtml(fmtDose(item.dose))} ${escapeHtml(item.unit)}</span>
        </div>`).join('');
}

function renderFeedTile({ today, irrigations }) {
    const tile = el('panel-feed');
    if (!tile) return;

    const badge = el('panel-feed-badge');
    const plan = planBadge(today);
    badge.className = `sensor-badge ${plan.state}`.trim();
    badge.textContent = plan.text;

    // No log at all is not an alarm: it is a tent nobody has watered yet, and
    // the tile already says what to do about that.
    const since = lastWateringLabel((irrigations || [])[0]?.date);
    tile.classList.toggle('is-stale', !!since?.stale);
    el('panel-feed-since').textContent = since ? since.text : 'Sin riegos';

    el('panel-feed-week').textContent = today
        ? (today.week
            ? `${stageName(today.stage)} · semana ${today.week} de ${today.weeks_in_stage}`
            : stageName(today.stage))
        : '--';

    // The volume the doses below were scaled against, so the two are read as
    // one instruction: "10 L, y en esos 10 L va esto".
    el('panel-feed-litres').textContent = Number.isFinite(today?.litres)
        ? fmtDose(today.litres) : '--';

    el('panel-feed-doses').innerHTML = dosesHtml(today);
}

// ---------------------------------------------------------------------------
// The watering CTA
// ---------------------------------------------------------------------------
// The part that is a prompt to act - that it has been a while - hung on the
// button in the header, and only when it has. The tile above says how long on
// every view of the panel; this is what carries that onto the other five.

function renderFeedBadge({ irrigations }) {
    const badge = document.getElementById('feed-cta-badge');
    if (!badge) return;

    const since = lastWateringLabel((irrigations || [])[0]?.date);
    if (!since) {
        badge.hidden = true;
        return;
    }

    badge.hidden = !since.stale;
    badge.textContent = `${since.days} d`;
    badge.title = `Último riego: ${irrigations[0].date}`;
}

function wirePanel() {
    document.addEventListener('feeding:updated', event => {
        const detail = event.detail || {};
        renderFeedBadge(detail);
        renderFeedTile(detail);
    });
}

export { PANEL_HOURS, fetchPanelHistory, wirePanel };
