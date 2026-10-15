/**
 * The bitácora of the panel: what happened to the tent this week, newest first.
 *
 * Grouping and filtering are pure (tests/js/logbook.test.mjs); the fetch and
 * the chips are the only part that touches the page.
 */

import { apiGet } from './api.js';
import { escapeHtml, parseLocalDatetime } from './util.js';

/** The filters, in the order the chips show them. `kind` null means all. */
const LOGBOOK_FILTERS = [
    { id: 'todo', label: 'Todo', kind: null },
    { id: 'riegos', label: 'Riegos', kind: 'riego' },
    { id: 'salidas', label: 'Salidas', kind: 'salida' },
    { id: 'cultivo', label: 'Cultivo', kind: 'cultivo' },
    { id: 'sistema', label: 'Sistema', kind: 'sistema' },
];

const WEEKDAYS = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb'];
const MONTHS = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];

function sameDay(a, b) {
    return a.getFullYear() === b.getFullYear()
        && a.getMonth() === b.getMonth()
        && a.getDate() === b.getDate();
}

/**
 * "Hoy", "Ayer", or "mié 13 oct". Named by the day, not by hours ago: the list
 * is read as a diary, and "hace 9 horas" is not where anything happened.
 */
function dayLabel(date, now = new Date()) {
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (sameDay(date, now)) return 'Hoy';
    if (sameDay(date, yesterday)) return 'Ayer';
    return `${WEEKDAYS[date.getDay()]} ${date.getDate()} ${MONTHS[date.getMonth()]}`;
}

/** The dot that marks one entry. Colour says what kind of thing it was. */
function dotClass(item) {
    if (item.kind === 'salida') {
        return /prendió/i.test(item.text || '') ? 'ac-dot--warn' : 'ac-dot--idle';
    }
    if (item.kind === 'riego') return 'ac-dot--info';
    if (item.kind === 'cultivo') return 'ac-dot--ok';
    return 'ac-dot--idle';
}

/** Items of one filter, in the order they came. */
function filterLogbook(items, filterId = 'todo') {
    const filter = LOGBOOK_FILTERS.find(f => f.id === filterId) || LOGBOOK_FILTERS[0];
    return (items || []).filter(item => !filter.kind || item.kind === filter.kind);
}

/**
 * The entries grouped by day, newest day first, as they came from the server.
 *
 * Entries whose datetime cannot be read are dropped rather than shown under a
 * made-up day: a row with no date is not something to act on.
 *
 * @returns {Array<{label: string, events: Array<{time: string, text: string, detail: string, dot: string}>}>}
 */
function groupLogbook(items, filterId = 'todo', now = new Date()) {
    const groups = [];
    let current = null;
    for (const item of filterLogbook(items, filterId)) {
        const date = parseLocalDatetime(item.datetime);
        if (Number.isNaN(date.getTime())) continue;

        const label = dayLabel(date, now);
        if (!current || current.label !== label) {
            current = { label, events: [] };
            groups.push(current);
        }
        const hh = String(date.getHours()).padStart(2, '0');
        const mm = String(date.getMinutes()).padStart(2, '0');
        current.events.push({
            time: `${hh}:${mm}`,
            text: item.text || '',
            detail: item.detail || '',
            dot: dotClass(item),
        });
    }
    return groups;
}

// ===============================
// Page
// ===============================

let entries = [];
let activeFilter = 'todo';

function renderLogbook() {
    const container = document.getElementById('panel-logbook');
    if (!container) return;

    const groups = groupLogbook(entries, activeFilter);
    if (!groups.length) {
        container.innerHTML = '<div class="ac-empty">Todavía no hay nada en la bitácora de esta semana.</div>';
        return;
    }

    container.innerHTML = `<div class="ac-log">${groups.map(group => `
        <div class="ac-log__day">${escapeHtml(group.label)}</div>
        ${group.events.map(event => `
            <div class="ac-log__item">
                <span class="ac-log__time">${escapeHtml(event.time)}</span>
                <span class="ac-dot ${event.dot}" aria-hidden="true"></span>
                <div>
                    <div class="ac-log__text">${escapeHtml(event.text)}</div>
                    ${event.detail ? `<div class="ac-log__detail">${escapeHtml(event.detail)}</div>` : ''}
                </div>
            </div>`).join('')}`).join('')}</div>`;
}

function fetchLogbook() {
    return apiGet('/api/logbook?days=7&limit=80', { key: 'logbook' }).then(data => {
        // A null here is a request already in flight, or a failed one: either
        // way the list on screen stays as it was.
        if (!data || !Array.isArray(data.items)) return;
        entries = data.items;
        renderLogbook();
    });
}

function setLogbookFilter(filterId) {
    activeFilter = LOGBOOK_FILTERS.some(f => f.id === filterId) ? filterId : 'todo';
    document.querySelectorAll('[data-logbook-filter]').forEach(button => {
        button.setAttribute('aria-pressed', String(button.dataset.logbookFilter === activeFilter));
    });
    renderLogbook();
}

function wireLogbook() {
    const chips = document.getElementById('panel-logbook-filters');
    if (!chips) return;
    chips.addEventListener('click', event => {
        const chip = event.target.closest('[data-logbook-filter]');
        if (chip) setLogbookFilter(chip.dataset.logbookFilter);
    });
    setLogbookFilter(activeFilter);
}

export {
    LOGBOOK_FILTERS, dayLabel, dotClass, filterLogbook, groupLogbook,
    fetchLogbook, wireLogbook,
};
