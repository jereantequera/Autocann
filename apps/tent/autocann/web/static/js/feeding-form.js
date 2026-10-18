/**
 * Riego, today's part: the subtitle under the heading and the form that logs a
 * watering. The form is the doses due this week, each one editable.
 */

import { doseAmount, escapeHtml, fmtDose, waterOnlyNote } from './util.js';
import { el, feedState, stageName } from './feeding-state.js';

// ---------------------------------------------------------------------------
// Today
// ---------------------------------------------------------------------------

/**
 * The subtitle under the heading, and a notice only when there is nothing to
 * pour. The doses themselves are the product rows: the form already multiplies
 * them by the litres, so a second list here would just say it twice.
 */
export function renderToday() {
    const notice = el('feed-doses');
    const week = el('feed-week');
    if (!notice || !week) return;

    if (!feedState.today) {
        notice.hidden = true;
        return;
    }

    const count = feedState.today.items.length;
    week.textContent = feedState.today.week
        ? `${stageName(feedState.today.stage)} · semana ${feedState.today.week} de ${feedState.today.weeks_in_stage}`
        : stageName(feedState.today.stage);
    if (count) {
        week.textContent += ` · el plan lleva ${count} ${count === 1 ? 'producto' : 'productos'}`;
    }

    const waterOnly = waterOnlyNote(feedState.today.water_only);
    if (waterOnly) {
        notice.hidden = false;
        notice.textContent = waterOnly.text;
        return;
    }
    if (!count) {
        notice.hidden = false;
        notice.textContent = 'No hay nada cargado en el plan para esta semana. '
            + 'Abrí «Plan por semana» y completá la grilla.';
        return;
    }
    notice.hidden = true;
    notice.textContent = '';
}

// ---------------------------------------------------------------------------
// The form
// ---------------------------------------------------------------------------

/**
 * One row per product in the chart, with the ones due this week already
 * ticked. Products off-plan stay available but are flagged once used, so an
 * unplanned addition is visible in the log afterwards.
 */
export function renderForm() {
    const box = el('feed-products');
    if (!box || !feedState.plan) return;

    const due = new Map((feedState.today?.items || []).map(item => [item.product_id, item.dose]));
    const ordered = [...feedState.plan.products].sort(
        (a, b) => (due.has(a.id) ? 0 : 1) - (due.has(b.id) ? 0 : 1)
    );

    if (!ordered.length) {
        box.innerHTML = '<div class="feed-flush">No hay productos cargados en el plan.</div>';
        return;
    }

    // A product outside this week's plan stays usable, but its row is dimmed:
    // an unplanned addition should look like one at a glance.
    box.innerHTML = ordered.map(product => {
        const planned = due.get(product.id);
        const id = Number(product.id);
        const name = escapeHtml(product.name);
        return `
        <label class="ac-product${planned ? '' : ' is-off'}" data-product-id="${id}">
            <input class="ac-check" type="checkbox" ${planned ? 'checked' : ''}
                   aria-label="${name}">
            <span class="ac-product__name">
                ${name}
                <span class="ac-product__note">${planned ? 'en el plan' : 'no está en el plan esta semana'}</span>
            </span>
            <span class="ac-product__dose">
                <input type="number" class="ac-field__input ac-field__input--num feed-product-dose"
                       min="0" step="0.5" inputmode="decimal"
                       value="${planned ? fmtDose(planned).replace(',', '.') : ''}"
                       aria-label="Dosis de ${name}">
                <small>${escapeHtml(product.unit)}</small>
            </span>
            <span class="ac-product__total"></span>
        </label>`;
    }).join('');

    refreshTotals();
}

export function refreshTotals() {
    const litres = Number(el('feed-litres').value) || 0;
    document.querySelectorAll('#feed-products .ac-product').forEach(row => {
        const checkbox = row.querySelector('input[type=checkbox]');
        const input = row.querySelector('.feed-product-dose');
        const total = row.querySelector('.ac-product__total');
        const value = Number(input.value) || 0;
        const active = checkbox.checked && value > 0;
        const product = feedState.plan.products.find(p => p.id === Number(row.dataset.productId));

        total.textContent = active && product
            ? doseAmount({ dose: value, unit: product.unit }, litres)
            : '--';
    });
}

export function readForm() {
    const planned = new Set((feedState.today?.items || []).map(item => item.product_id));
    const items = [];
    document.querySelectorAll('#feed-products .ac-product').forEach(row => {
        const checkbox = row.querySelector('input[type=checkbox]');
        const value = Number(row.querySelector('.feed-product-dose').value) || 0;
        if (!checkbox.checked || value <= 0) return;

        const id = Number(row.dataset.productId);
        const product = feedState.plan.products.find(p => p.id === id);
        items.push({
            product_id: id,
            name: product ? product.name : 'Sin nombre',
            unit: product ? product.unit : 'ml/L',
            dose: value,
            on_plan: planned.has(id),
        });
    });
    return items;
}
