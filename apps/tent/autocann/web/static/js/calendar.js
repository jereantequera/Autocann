/**
 * The irrigation calendar.
 *
 * The log answers "when did I last water". A month on a grid answers the
 * question worth asking — am I watering on a rhythm, and where are the gaps —
 * which a reverse-chronological list hides by construction.
 *
 * It renders whatever the feeding module last fetched; it owns no request of
 * its own, so the two can never show different logs.
 */

import { escapeHtml } from './util.js';
import { config } from './state.js';

const MONTHS = [
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
];

/** The month on screen, as the first day of it. */
let month = startOfMonth(new Date());
/** Irrigations by "YYYY-MM-DD"; one day can hold more than one. */
let byDate = new Map();
/** The day whose detail panel is open, or null. */
let openDate = null;

function startOfMonth(date) {
    return new Date(date.getFullYear(), date.getMonth(), 1);
}

/** "YYYY-MM-DD" in local time. `toISOString` would shift the day in UTC-3. */
function isoDate(date) {
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${date.getFullYear()}-${month}-${day}`;
}

/** Monday-first weekday index, because that is how the week is read here. */
function weekdayIndex(date) {
    return (date.getDay() + 6) % 7;
}

function dose(value) {
    if (typeof value !== 'number' || !Number.isFinite(value)) return '--';
    return Number.isInteger(value) ? String(value) : value.toFixed(1).replace('.', ',');
}

function stageName(stage) {
    return config.stageNames[stage] || stage || '';
}

// ---------------------------------------------------------------------------
// The grid
// ---------------------------------------------------------------------------

function dayClasses(entries, date, today) {
    const classes = ['cal-day'];
    if (date.getTime() === today.getTime()) classes.push('is-today');
    if (date > today) classes.push('is-future');
    if (entries.length) {
        classes.push('has-feed');
        // Something poured that the chart did not call for is worth spotting a
        // month later, which is the whole point of looking at the grid.
        if (entries.some(entry => (entry.items || []).some(item => !item.on_plan))) {
            classes.push('is-off-plan');
        } else if (entries.every(entry => !(entry.items || []).length)) {
            classes.push('is-flush');
        }
    }
    return classes.join(' ');
}

function render() {
    const grid = document.getElementById('cal-grid');
    const label = document.getElementById('cal-month');
    if (!grid) return;

    if (label) label.textContent = `${MONTHS[month.getMonth()]} ${month.getFullYear()}`;

    const today = new Date();
    today.setHours(0, 0, 0, 0);

    const cells = [];
    // Lead-in blanks so the 1st lands under its weekday.
    for (let i = 0; i < weekdayIndex(month); i += 1) {
        cells.push('<div class="cal-day is-blank" aria-hidden="true"></div>');
    }

    const daysInMonth = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
    for (let day = 1; day <= daysInMonth; day += 1) {
        const date = new Date(month.getFullYear(), month.getMonth(), day);
        const key = isoDate(date);
        const entries = byDate.get(key) || [];
        const litres = entries.reduce((total, entry) => total + (Number(entry.litres) || 0), 0);

        cells.push(`
            <button type="button" class="${dayClasses(entries, date, today)}${openDate === key ? ' is-open' : ''}"
                    data-cal-day="${key}" ${entries.length ? '' : 'disabled'}
                    title="${escapeHtml(key)}${entries.length ? ` · ${dose(litres)} L` : ''}">
                <span class="cal-day-num">${day}</span>
                ${entries.length ? `<span class="cal-day-litres">${escapeHtml(dose(litres))} L</span>` : ''}
                ${entries.length ? '<span class="cal-dot"></span>' : ''}
            </button>`);
    }

    grid.innerHTML = cells.join('');
    renderStreak(today);
    renderDetail();
}

/**
 * How long since the last watering, and the usual gap between them.
 *
 * The average is what makes a gap legible: "hace 6 días" means nothing until
 * you know whether this tent is watered every two days or every seven.
 */
function renderStreak(today) {
    const box = document.getElementById('cal-streak');
    if (!box) return;

    const dates = [...byDate.keys()].sort().reverse();
    if (!dates.length) {
        box.textContent = 'Sin riegos registrados todavía.';
        return;
    }

    const last = new Date(`${dates[0]}T00:00:00`);
    const days = Math.round((today - last) / 86400000);

    let cadence = '';
    if (dates.length >= 3) {
        const recent = dates.slice(0, 8).map(key => new Date(`${key}T00:00:00`));
        const gaps = recent.slice(1).map((date, i) => (recent[i] - date) / 86400000);
        const average = gaps.reduce((a, b) => a + b, 0) / gaps.length;
        cadence = ` · suele ser cada ${average.toFixed(1).replace('.', ',')} días`;
    }

    box.textContent = days <= 0
        ? `Regado hoy${cadence}`
        : `Último riego hace ${days} ${days === 1 ? 'día' : 'días'}${cadence}`;
}

// ---------------------------------------------------------------------------
// One day
// ---------------------------------------------------------------------------

function renderDetail() {
    const box = document.getElementById('cal-detail');
    if (!box) return;

    const entries = openDate ? byDate.get(openDate) : null;
    if (!entries || !entries.length) {
        box.hidden = true;
        box.innerHTML = '';
        return;
    }

    box.hidden = false;
    // The same log rows the Riego column lists, so a day reads the same here.
    box.innerHTML = entries.map(entry => {
        const items = (entry.items || []).map(item => `
            <span class="ac-log__text${item.on_plan ? '' : ' is-off'}">
                ${escapeHtml(item.name)} ${escapeHtml(dose(item.dose))} ${escapeHtml(item.unit)}
            </span>`).join('') || '<span class="ac-log__text">Solo agua</span>';

        const measures = [
            entry.stage ? `${stageName(entry.stage)} S${Number(entry.week) || '?'}` : '',
            Number.isFinite(entry.ph) ? `pH ${dose(entry.ph)}` : '',
            Number.isFinite(entry.ec) ? `EC ${dose(entry.ec)}` : '',
        ].filter(Boolean).join(' · ');

        return `
            <div class="cal-detail-head">
                <strong>${escapeHtml(entry.date)}</strong>
                <span>${escapeHtml(dose(entry.litres))} L</span>
            </div>
            <div class="ac-log__item">
                <span class="ac-dot ac-dot--info" aria-hidden="true"></span>
                <div>
                    ${items}
                    ${measures ? `<div class="ac-log__detail">${escapeHtml(measures)}</div>` : ''}
                    ${entry.notes ? `<div class="ac-log__detail">${escapeHtml(entry.notes)}</div>` : ''}
                </div>
            </div>`;
    }).join('');
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function setIrrigations(irrigations) {
    byDate = new Map();
    (irrigations || []).forEach(entry => {
        if (!entry || !entry.date) return;
        const list = byDate.get(entry.date) || [];
        list.push(entry);
        byDate.set(entry.date, list);
    });
    // A day that disappeared (deleted from the log) must not keep its panel open.
    if (openDate && !byDate.has(openDate)) openDate = null;
    render();
}

function shiftMonth(delta) {
    month = new Date(month.getFullYear(), month.getMonth() + delta, 1);
    openDate = null;
    render();
}

function wireCalendar() {
    const panel = document.getElementById('cal-panel');
    if (!panel) return;

    // Its own delegated listener rather than entries in the global action map:
    // month paging is this widget's business and nobody else's.
    panel.addEventListener('click', event => {
        const nav = event.target.closest('[data-cal]');
        if (nav) {
            if (nav.dataset.cal === 'prev') shiftMonth(-1);
            else if (nav.dataset.cal === 'next') shiftMonth(1);
            else { month = startOfMonth(new Date()); openDate = null; render(); }
            return;
        }

        const day = event.target.closest('[data-cal-day]');
        if (!day) return;
        const key = day.dataset.calDay;
        openDate = openDate === key ? null : key;
        render();
    });

    document.addEventListener('feeding:updated', event => {
        setIrrigations(event.detail?.irrigations);
    });

    render();
}

export { wireCalendar, setIrrigations };
