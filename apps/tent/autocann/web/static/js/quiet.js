/**
 * Quiet hours: the window in which the controller may not start an output.
 *
 * The backend has answered both verbs since quiet hours were added and nothing
 * called it, so the one setting that keeps a loud fan from starting at four in
 * the morning was reachable only with curl.
 *
 * It constrains the decision, not the relay, and this screen is careful to say
 * so: nothing here forces a contact, stops something already running, or
 * overrides a person.
 */

import { apiGet } from './api.js';
import { escapeHtml, fmtNum, icon, iconText } from './util.js';

/** Last payload of /api/quiet-hours, and the copy the form edits. */
let served = null;
let draft = {};

/** Which actions the loop says it is holding off right now, from the sensors. */
let blockedNow = [];

/** The controller action each output is energised by; only these can be silenced. */
const ACTION_LABELS = {
    humidity_up: 'humidificar',
    humidity_down: 'bajar humedad',
};

function el(id) {
    return document.getElementById(id);
}

function windowFor(name) {
    return draft[name] || { enabled: false, start: null, end: null, max_humidity: null };
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

/**
 * A window on an output nothing drives automatically blocks nothing.
 *
 * The backend serves `automatic_outputs` precisely so this can be said out
 * loud rather than offering a setting that is quietly inert.
 */
function inertCardHtml(output) {
    return `
        <div class="quiet-card is-inert">
            <div class="quiet-head">
                <span class="quiet-name">${escapeHtml(output.label || output.name)}</span>
            </div>
            <div class="quiet-inert-note">
                Nada la enciende automáticamente, así que una franja acá no
                silenciaría nada. Sólo se mueve a mano.
            </div>
        </div>`;
}

function cardHtml(output) {
    const name = escapeHtml(output.name);
    const w = windowFor(output.name);
    const on = !!w.enabled;
    const action = ACTION_LABELS[output.name];
    const silencing = blockedNow.includes(output.name);

    // "Sin tope" is not the same as 0: one is a hard block, the other silences
    // nothing at all. An empty field has to read as the first.
    const cap = (typeof w.max_humidity === 'number' && Number.isFinite(w.max_humidity))
        ? fmtNum(w.max_humidity, 0) : '';

    return `
        <div class="quiet-card${on ? ' is-on' : ''}${silencing ? ' is-silencing' : ''}"
             data-quiet-output="${name}">
            <div class="quiet-head">
                <label class="quiet-toggle">
                    <input type="checkbox" data-quiet-field="enabled" ${on ? 'checked' : ''}>
                    <span class="quiet-name">${escapeHtml(output.label || output.name)}</span>
                </label>
                ${silencing ? `<span class="sensor-badge ok">${icon('bell-off')}silenciando ahora</span>` : ''}
            </div>
            ${action ? `<div class="quiet-action">El lazo no va a elegir
                «${escapeHtml(action)}» dentro de la franja.</div>` : ''}

            <div class="quiet-fields">
                <label>
                    Desde
                    <input type="time" data-quiet-field="start" value="${escapeHtml(w.start || '')}">
                </label>
                <label>
                    Hasta
                    <input type="time" data-quiet-field="end" value="${escapeHtml(w.end || '')}">
                </label>
                <label>
                    Salvo humedad ≥
                    <input type="number" min="0" max="100" step="1" inputmode="decimal"
                           placeholder="sin tope"
                           data-quiet-field="max_humidity" value="${escapeHtml(cap)}">
                </label>
            </div>
            <div class="quiet-hint" data-quiet-hint></div>
        </div>`;
}

/**
 * Said in words, because "23:00 → 07:00" is two intervals on a clock face.
 *
 * @returns {{icon?: string, text: string}}  an icon only when it is a warning
 */
function describe(w) {
    if (!w.enabled) return { text: 'Sin franja: el lazo decide a cualquier hora.' };
    if (!w.start || !w.end) {
        return { icon: 'triangle-alert', text: 'Falta la hora de inicio o de fin; así no se aplica.' };
    }
    if (w.start === w.end) return { icon: 'triangle-alert', text: 'Una franja de ancho cero no silencia nada.' };

    const crosses = w.start > w.end ? ' (cruza la medianoche)' : '';
    const cap = (typeof w.max_humidity === 'number' && Number.isFinite(w.max_humidity))
        ? `, salvo que la humedad llegue a ${fmtNum(w.max_humidity, 0)} %`
        // Said every time it is empty: a hard block is choosing quiet over the
        // plants, which is allowed, but not by accident.
        : ', sin excepción por humedad';
    return { text: `De ${w.start} a ${w.end}${crosses}${cap}.` };
}

function renderHints() {
    document.querySelectorAll('[data-quiet-output]').forEach(card => {
        const hint = card.querySelector('[data-quiet-hint]');
        if (hint) hint.innerHTML = iconText(describe(windowFor(card.dataset.quietOutput)));
    });
}

function render() {
    const grid = el('quiet-grid');
    const badge = el('quiet-badge');
    if (!grid || !served) return;

    const automatic = served.automatic_outputs || [];
    const cards = (served.outputs || []).map(output => (
        automatic.includes(output.name) ? cardHtml(output) : inertCardHtml(output)
    ));
    grid.innerHTML = cards.join('') || '<div class="sys-note">No hay salidas configuradas.</div>';
    renderHints();

    if (badge) {
        const active = automatic.filter(name => windowFor(name).enabled).length;
        badge.className = active ? 'sensor-badge ok' : 'sensor-badge';
        badge.textContent = active
            ? `${active} ${active === 1 ? 'franja activa' : 'franjas activas'}`
            : 'ninguna';
    }
}

// ---------------------------------------------------------------------------
// Editing
// ---------------------------------------------------------------------------

function readField(card, field) {
    const input = card.querySelector(`[data-quiet-field="${field}"]`);
    if (!input) return null;
    if (field === 'enabled') return input.checked;
    if (field === 'max_humidity') {
        const raw = input.value.trim();
        return raw === '' ? null : Number(raw);
    }
    return input.value || null;
}

function syncFromForm(card) {
    const name = card.dataset.quietOutput;
    draft[name] = {
        enabled: readField(card, 'enabled'),
        start: readField(card, 'start'),
        end: readField(card, 'end'),
        max_humidity: readField(card, 'max_humidity'),
    };
    card.classList.toggle('is-on', !!draft[name].enabled);
}

async function save() {
    const state = el('quiet-state');
    state.textContent = 'Guardando…';
    state.className = 'quiet-state';

    try {
        const response = await fetch('/api/quiet-hours', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ windows: draft }),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);

        state.textContent = result.note || 'Guardado';
        state.className = 'quiet-state is-ok';
        await fetchQuietHours();
    } catch (error) {
        console.error('Error saving quiet hours:', error);
        state.textContent = error.message;
        state.className = 'quiet-state is-error';
    }
}

// ---------------------------------------------------------------------------
// Network and wiring
// ---------------------------------------------------------------------------

async function fetchQuietHours() {
    const data = await apiGet('/api/quiet-hours', { key: 'quiet-hours' });
    if (!data || !data.outputs) return;
    served = data;
    draft = JSON.parse(JSON.stringify(data.windows || {}));
    render();
    fetchBlockedNow();
}

/**
 * Asked once on arrival rather than polled.
 *
 * The live reading is on a 3 s poller, but only on the panel - and adding this
 * view to it would put the Raspberry back to answering every three seconds for
 * a badge that changes twice a day.
 */
function fetchBlockedNow() {
    apiGet('/api/current-data', { key: 'quiet-current' })
        .then(data => { if (data && !data.error) setBlockedActions(data.quiet_hours); });
}

/**
 * Which actions the loop is holding off at this moment.
 *
 * Published by the control loop with the rest of the reading, so the card can
 * say "silenciando ahora" from the loop's own answer rather than from the
 * browser re-deciding it against a clock that may not agree.
 */
function setBlockedActions(actions) {
    const outputs = (actions || [])
        .map(action => (action === 'humidify' ? 'humidity_up'
            : action === 'dehumidify' ? 'humidity_down' : null))
        .filter(Boolean);
    const changed = outputs.join() !== blockedNow.join();
    blockedNow = outputs;
    if (changed && served) render();
}

function wireQuiet() {
    const grid = el('quiet-grid');
    if (!grid) return;

    grid.addEventListener('input', event => {
        const card = event.target.closest('[data-quiet-output]');
        if (card) { syncFromForm(card); renderHints(); }
    });
    grid.addEventListener('change', event => {
        const card = event.target.closest('[data-quiet-output]');
        if (card) { syncFromForm(card); renderHints(); }
    });

    document.addEventListener('click', event => {
        if (event.target.closest('[data-quiet="save"]')) save();
    });

    document.addEventListener('view:enter', event => {
        if (event.detail?.view === 'sistema') fetchQuietHours();
    });
}

export { wireQuiet, fetchQuietHours, setBlockedActions };
