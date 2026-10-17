import { apiGet, apiPut } from './api.js';
import { toast } from './modals.js';
import { lastWateringLabel } from './util.js';

/**
 * The "Próximo riego" card: when the next watering is due, and the interval
 * that decides it. The server does the date arithmetic; this module only words
 * it and sends the interval back.
 */

const MIN_DAYS = 1;
const MAX_DAYS = 30;

const WEEKDAYS = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'];

/** "1 día" or "2 días". */
function dias(count) {
    return count === 1 ? '1 día' : `${count} días`;
}

/** "jueves 23", from an ISO day. */
function weekdayAndDay(isoDay) {
    const day = new Date(`${isoDay}T00:00:00`);
    return `${WEEKDAYS[day.getDay()]} ${day.getDate()}`;
}

/** "el martes": the weekday alone, for when a watering was supposed to happen. */
function weekdayOnly(isoDay) {
    return WEEKDAYS[new Date(`${isoDay}T00:00:00`).getDay()];
}

/** "Último riego: hace 2 días", or '' when there has never been one. */
function lastLine(isoDay) {
    const label = lastWateringLabel(isoDay);
    return label ? `Último riego: ${label.text.toLowerCase()}` : '';
}

/**
 * The words for the card, as a pure function of the /api/irrigation/next payload.
 *
 * Returns `{ headline, detail, tone }`. `tone` is '' (plain), 'today',
 * 'overdue' or 'unset', and only picks the colour of the headline.
 */
export function nextIrrigationCopy(payload) {
    if (!payload || payload.error) {
        return { headline: '--', detail: '', tone: '' };
    }
    if (!payload.configured) {
        return { headline: 'Configurá cada cuántos días regás', detail: '', tone: 'unset' };
    }

    const next = payload.next;
    const last = lastLine(payload.last_irrigation_date);
    switch (next.state) {
        case 'today':
            return { headline: 'Toca regar hoy', detail: last, tone: 'today' };
        case 'no_history':
            return {
                headline: 'Toca regar hoy',
                detail: 'Todavía no hay riegos registrados en este cultivo.',
                tone: 'today',
            };
        case 'overdue':
            return {
                headline: `Atrasado ${dias(next.overdue_days)} — tocaba el ${weekdayOnly(next.due_date)}`,
                detail: last,
                tone: 'overdue',
            };
        default:
            return {
                headline: `Próximo riego: ${weekdayAndDay(next.due_date)} (en ${dias(next.days_until)})`,
                detail: last,
                tone: '',
            };
    }
}

/**
 * The interval the grower typed. Empty means "no interval" (sent as null); a
 * whole number from 1 to 30 is an interval; anything else is refused here, so
 * the server only ever sees what it accepts.
 */
export function parseIntervalInput(raw) {
    const text = String(raw ?? '').trim();
    if (text === '') return { ok: true, days: null };
    const days = Number(text);
    if (/^\d+$/.test(text) && days >= MIN_DAYS && days <= MAX_DAYS) {
        return { ok: true, days };
    }
    return { ok: false, error: `Ponete un número entero de ${MIN_DAYS} a ${MAX_DAYS} días.` };
}

// ---------------------------------------------------------------------------
// The card
// ---------------------------------------------------------------------------

/** Last payload of /api/irrigation/next. */
let current = null;

function el(id) {
    return document.getElementById(id);
}

function render() {
    const headline = el('irrigation-next-headline');
    if (!headline) return;

    const copy = nextIrrigationCopy(current);
    headline.textContent = copy.headline;
    headline.className = 'irrigation-next__headline'
        + (copy.tone ? ` irrigation-next__headline--${copy.tone}` : '');
    el('irrigation-next-detail').textContent = copy.detail;

    // The input follows the server, unless the grower is typing in it.
    const input = el('irrigation-interval');
    if (input && document.activeElement !== input) {
        input.value = current?.configured ? current.interval_days : '';
    }
}

async function fetchIrrigationNext() {
    const payload = await apiGet('/api/irrigation/next');
    if (payload) {
        current = payload;
        render();
    }
}

async function saveInterval(event) {
    event.preventDefault();
    const input = el('irrigation-interval');
    const error = el('irrigation-interval-error');

    const parsed = parseIntervalInput(input.value);
    if (!parsed.ok) {
        error.textContent = parsed.error;
        input.setAttribute('aria-invalid', 'true');
        input.focus();
        return;
    }
    error.textContent = '';
    input.removeAttribute('aria-invalid');

    const result = await apiPut('/api/irrigation/interval', { days: parsed.days });
    if (!result.ok) {
        error.textContent = result.error;
        return;
    }
    toast(parsed.days === null
        ? 'Quitado: no hay recordatorio de riego'
        : `Riego cada ${dias(parsed.days)}`);
    await fetchIrrigationNext();
}

/**
 * Attach the form, and refresh the card whenever a watering is logged, so the
 * date it shows is never the one from before the watering.
 */
function wireIrrigationNext() {
    const form = el('irrigation-interval-form');
    if (!form) return;
    form.addEventListener('submit', saveInterval);
    document.addEventListener('feeding:updated', fetchIrrigationNext);
}

export { fetchIrrigationNext, wireIrrigationNext };
