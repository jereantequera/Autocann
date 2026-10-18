/**
 * Riego, the log: the irrigation list with the measures taken with each one, and
 * the delete control on every entry.
 */

import { doseAmount, escapeHtml, fmtDose, lastWateringLabel } from './util.js';
import { formatShortDate } from './grow-model.js';
import { el, feedState, stageName } from './feeding-state.js';

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------

/**
 * The irrigation log as a day-by-day list: what was poured, the measures taken
 * with it, and the note, with the delete control at the foot of each entry.
 */
export function renderHistory() {
    const box = el('feed-history');
    const badge = el('feed-last-badge');
    if (!box) return;

    if (!feedState.history.length) {
        box.innerHTML = `
            <div class="ac-empty">
                Todavía no hay riegos en este cultivo. El primero que guardes
                aparece acá y en el calendario.
            </div>`;
        if (badge) badge.textContent = 'sin riegos';
        return;
    }

    // The same helper the panel tile reads, so the two badges cannot disagree
    // about how long it has been - they used to count from different hours of
    // the day and cross over at different moments.
    const since = lastWateringLabel(feedState.history[0].date);
    if (badge && since) {
        badge.textContent = since.days <= 0 ? 'regado hoy'
            : `hace ${since.days} ${since.days === 1 ? 'día' : 'días'}`;
        badge.classList.toggle('stale', since.stale);
    }

    box.innerHTML = feedState.history.map(entry => {
        const id = Number(entry.id);
        const items = (entry.items || []).map(item => `
            <span class="ac-log__text${item.on_plan ? '' : ' is-off'}">
                ${escapeHtml(item.name)} ${escapeHtml(fmtDose(item.dose))} ${escapeHtml(item.unit)}
                · ${escapeHtml(doseAmount(item, entry.litres))}${item.on_plan ? '' : ' · fuera del plan'}
            </span>`).join('') || '<span class="ac-log__text">Solo agua</span>';

        const measures = [
            entry.stage ? `${escapeHtml(stageName(entry.stage))} S${Number(entry.week) || '?'}` : '',
            Number.isFinite(entry.ph) ? `pH ${escapeHtml(fmtDose(entry.ph))}` : '',
            Number.isFinite(entry.ec) ? `EC ${escapeHtml(fmtDose(entry.ec))}` : '',
        ].filter(Boolean).join(' · ');

        return `
        <div class="ac-log__day">${escapeHtml(formatShortDate(entry.date))}</div>
        <div class="ac-log__item">
            <span class="ac-log__time">${escapeHtml(fmtDose(entry.litres))} L</span>
            <span class="ac-dot ac-dot--info" aria-hidden="true"></span>
            <div>
                ${items}
                ${measures ? `<div class="ac-log__detail">${measures}</div>` : ''}
                ${entry.notes ? `<div class="ac-log__detail">${escapeHtml(entry.notes)}</div>` : ''}
                <button class="ac-btn ac-btn--ghost feed-delete" data-irrigation-id="${id}"
                        data-irrigation-date="${escapeHtml(entry.date)}">
                    Borrar
                </button>
            </div>
        </div>`;
    }).join('');
}
