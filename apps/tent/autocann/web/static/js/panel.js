/**
 * The panel's own pieces: the status line, the watering strip, the two tabs,
 * and the hourly history that feeds the VPD chart's trail.
 *
 * They exist because the panel must not depend on anything selected somewhere
 * else: its history is fixed at 24 hours rather than reading the range selector
 * that lives in Estadísticas, and the watering strip renders the plan the
 * feeding module already fetched.
 */

import { apiGet } from './api.js';
import { latestHealth } from './health.js';
import { fetchLogbook, wireLogbook } from './logbook.js';
import { config } from './state.js';
import { panelStatus } from './status.js';
import { setVpdCurrent, setVpdTrail, wireVpdChart } from './vpdchart.js';
import { doseAmount, fmtDose, lastWateringLabel, waterOnlyNote } from './util.js';

const TABS = ['ahora', 'bitacora'];

/** The dot's colour for each status level, from components.css. */
const LEVEL_DOT = { ok: 'ac-dot--ok', warn: 'ac-dot--warn', alert: 'ac-dot--alert', idle: 'ac-dot--idle' };

/** What the status line is judged on. Each event updates one part of it. */
const latest = { current: null, outputs: [], testMode: null };

function el(id) {
    return document.getElementById(id);
}

// ---------------------------------------------------------------------------
// Estado: the one sentence at the top of the left column
// ---------------------------------------------------------------------------
// Re-judged on any of the three things it depends on - the reading, the relays
// and the control loop's health - because each can change the verdict alone.

function renderStatus() {
    const dot = el('panel-status-dot');
    const title = el('panel-status-title');
    const text = el('panel-status-text');
    if (!dot || !title || !text) return;

    const status = panelStatus(latest.current, latestHealth(), latest.testMode,
        latest.outputs, config.stageNames);
    dot.className = `ac-dot ac-dot--lg ${LEVEL_DOT[status.level] || LEVEL_DOT.idle}`;
    title.textContent = status.title;
    text.textContent = status.text;
}

// ---------------------------------------------------------------------------
// Riego de hoy
// ---------------------------------------------------------------------------
// It renders the payload the feeding module already has rather than fetching
// its own, so the strip and the Riego view cannot show two different plans.

/** "Grow", not "pH Perfect Grow": the name on the jug is not what the eye needs. */
function shortProductName(name) {
    return String(name || '').replace(/^pH Perfect\s+/i, '');
}

function dosesLine(today) {
    if (!today) return 'Cargando plan';
    const waterOnly = waterOnlyNote(today.water_only);
    if (waterOnly) return waterOnly.text;
    if (!(today.items || []).length) return 'No hay nada cargado en el plan para esta semana.';
    return today.items
        .map(item => `${shortProductName(item.name)} ${doseAmount(item, today.litres)}`)
        .join(' · ');
}

function renderFeedTile({ today, irrigations }) {
    const title = el('panel-feed-title');
    if (!title) return;

    // The volume the doses were scaled against, so the title and the line
    // below are read as one instruction: "10 L, y en esos 10 L va esto".
    const litres = Number.isFinite(today?.litres) ? `${fmtDose(today.litres)} L` : '--';
    title.textContent = `Riego de hoy · ${litres}`;
    el('panel-feed-doses').textContent = dosesLine(today);

    const since = lastWateringLabel((irrigations || [])[0]?.date);
    el('panel-feed-since').textContent = since ? `Último riego · ${since.text}` : 'Sin riegos';
}

/** The sidebar's Agregar riego badge: how long since the last watering. */
function renderFeedBadge({ irrigations }) {
    const badge = el('feed-cta-badge');
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

// ---------------------------------------------------------------------------
// Tabs: Ahora | Bitácora. Kept in memory only, not in the URL.
// ---------------------------------------------------------------------------

function selectTab(name, { focus = false } = {}) {
    for (const tab of TABS) {
        const button = el(`panel-tab-${tab}`);
        const pane = el(`panel-${tab}`);
        if (!button || !pane) continue;

        const on = tab === name;
        button.setAttribute('aria-selected', String(on));
        // Roving tabindex: Tab enters the tablist once, and the arrows move on.
        button.tabIndex = on ? 0 : -1;
        pane.hidden = !on;
        if (on && focus) button.focus();
    }
    // The log is fetched when it is looked at, not only on the poll.
    if (name === 'bitacora') fetchLogbook();
}

function wireTabs() {
    const list = el('panel-tabs');
    if (!list) return;

    list.addEventListener('click', event => {
        const tab = event.target.closest('[data-panel-tab]');
        if (tab) selectTab(tab.dataset.panelTab);
    });

    list.addEventListener('keydown', event => {
        const index = TABS.findIndex(name => el(`panel-tab-${name}`)?.getAttribute('aria-selected') === 'true');
        const last = TABS.length - 1;
        const next = {
            ArrowRight: (index + 1) % TABS.length,
            ArrowLeft: (index - 1 + TABS.length) % TABS.length,
            Home: 0,
            End: last,
        }[event.key];
        if (next === undefined) return;
        event.preventDefault();
        selectTab(TABS[next], { focus: true });
    });
}

// ---------------------------------------------------------------------------
// History: the trail behind the VPD dot
// ---------------------------------------------------------------------------

/** Fixed at a day, deliberately: a wall screen has nobody to pick a range on it. */
function fetchPanelHistory() {
    apiGet('/api/history/aggregated?days=1&interval=hourly', { key: 'panel-history' })
        .then(data => {
            if (data && Array.isArray(data.data)) setVpdTrail(data.data);
        });
}

function wirePanel() {
    document.addEventListener('feeding:updated', event => {
        const detail = event.detail || {};
        renderFeedBadge(detail);
        renderFeedTile(detail);
    });

    document.addEventListener('current:updated', event => {
        latest.current = event.detail || null;
        renderStatus();
        setVpdCurrent(latest.current);
    });

    document.addEventListener('outputs:updated', event => {
        latest.outputs = event.detail?.outputs || [];
        latest.testMode = event.detail?.test_mode || null;
        renderStatus();
    });

    document.addEventListener('health:updated', renderStatus);

    wireVpdChart();
    wireTabs();
    wireLogbook();
    selectTab('ahora');
}

export { fetchPanelHistory, wirePanel };
