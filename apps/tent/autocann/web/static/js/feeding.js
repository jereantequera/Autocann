import { apiGet } from './api.js';
import { alertDialog, confirmDialog, toast } from './modals.js';
import { config } from './state.js';
import { doseAmount, escapeHtml, fmtDose, lastWateringLabel, waterOnlyNote } from './util.js';
import { formatShortDate } from './grow-model.js';
import { currentColumnIndex, stageGroups, stageLabel } from './feed-grid.js';

/**
 * Feeding: today's doses, the irrigation log, and the editor for the chart.
 *
 * The chart lives in the database and the stage comes from the active grow, so
 * this module holds no schedule of its own — only what the server last sent.
 */

/** Last payload of /api/feeding/today, so the form can be reset to the plan. */
let today = null;
/** Last payload of /api/feed-plan, and the working copy the editor edits. */
let plan = null;
let planDraft = null;
/** The log rows. */
let history = [];
/** Set once the grower edits the form, so a poll does not overwrite their input. */
let formTouched = false;

const UNITS = ['ml/L', 'g/L'];

const REMOVE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" '
    + 'stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
const ADD_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" '
    + 'stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';

function el(id) {
    return document.getElementById(id);
}

/**
 * Tell the calendar and the panel tile what the log says.
 *
 * They render the same rows this module fetched instead of asking for them
 * again, so three places cannot end up showing three different logs.
 */
function announce() {
    document.dispatchEvent(new CustomEvent('feeding:updated', {
        detail: { today, irrigations: history },
    }));
}

function stageName(stage) {
    return config.stageNames[stage] || stage || '--';
}

// ---------------------------------------------------------------------------
// Today
// ---------------------------------------------------------------------------

/**
 * The subtitle under the heading, and a notice only when there is nothing to
 * pour. The doses themselves are the product rows: the form already multiplies
 * them by the litres, so a second list here would just say it twice.
 */
function renderToday() {
    const notice = el('feed-doses');
    const week = el('feed-week');
    if (!notice || !week) return;

    if (!today) {
        notice.hidden = true;
        return;
    }

    const count = today.items.length;
    week.textContent = today.week
        ? `${stageName(today.stage)} · semana ${today.week} de ${today.weeks_in_stage}`
        : stageName(today.stage);
    if (count) {
        week.textContent += ` · el plan lleva ${count} ${count === 1 ? 'producto' : 'productos'}`;
    }

    const waterOnly = waterOnlyNote(today.water_only);
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
function renderForm() {
    const box = el('feed-products');
    if (!box || !plan) return;

    const due = new Map((today?.items || []).map(item => [item.product_id, item.dose]));
    const ordered = [...plan.products].sort(
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

function refreshTotals() {
    const litres = Number(el('feed-litres').value) || 0;
    document.querySelectorAll('#feed-products .ac-product').forEach(row => {
        const checkbox = row.querySelector('input[type=checkbox]');
        const input = row.querySelector('.feed-product-dose');
        const total = row.querySelector('.ac-product__total');
        const value = Number(input.value) || 0;
        const active = checkbox.checked && value > 0;
        const product = plan.products.find(p => p.id === Number(row.dataset.productId));

        total.textContent = active && product
            ? doseAmount({ dose: value, unit: product.unit }, litres)
            : '--';
    });
}

function readForm() {
    const planned = new Set((today?.items || []).map(item => item.product_id));
    const items = [];
    document.querySelectorAll('#feed-products .ac-product').forEach(row => {
        const checkbox = row.querySelector('input[type=checkbox]');
        const value = Number(row.querySelector('.feed-product-dose').value) || 0;
        if (!checkbox.checked || value <= 0) return;

        const id = Number(row.dataset.productId);
        const product = plan.products.find(p => p.id === id);
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

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------

/**
 * The irrigation log as a day-by-day list: what was poured, the measures taken
 * with it, and the note, with the delete control at the foot of each entry.
 */
function renderHistory() {
    const box = el('feed-history');
    const badge = el('feed-last-badge');
    if (!box) return;

    if (!history.length) {
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
    const since = lastWateringLabel(history[0].date);
    if (badge && since) {
        badge.textContent = since.days <= 0 ? 'regado hoy'
            : `hace ${since.days} ${since.days === 1 ? 'día' : 'días'}`;
        badge.classList.toggle('stale', since.stale);
    }

    box.innerHTML = history.map(entry => {
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

// ---------------------------------------------------------------------------
// The plan editor
// ---------------------------------------------------------------------------

function cloneplan() {
    return JSON.parse(JSON.stringify(plan?.products || []));
}

function markDirty() {
    const state = el('feed-plan-state');
    if (state) state.textContent = 'Cambios sin guardar';
}

function renderPlanEditor() {
    const table = el('feed-plan-table');
    if (!table || !plan) return;
    if (!planDraft) planDraft = cloneplan();

    const columns = plan.columns;
    const groups = stageGroups(columns);
    // A stage starts a new group: that is where the hairline goes.
    const starts = new Set(groups.map(group => group.start));

    // The header and the body must have the same cells in the same order, or
    // the weeks drift off their columns. So the product block is one cell in
    // each header row, and <col> fixes every week to the same width.
    const cols = `<colgroup><col class="feed-col-product">${columns.map(() => '<col class="feed-col-week">').join('')}</colgroup>`;
    const stageRow = groups.map(group => {
        const full = stageName(group.stage);
        return `<th scope="colgroup" colspan="${group.span}" class="feed-stage${starts.has(group.start) ? ' stage-start' : ''}"
            title="${escapeHtml(full)}">${escapeHtml(stageLabel(group.stage, group.span, full))}</th>`;
    }).join('');
    const weekRow = columns.map((column, position) => `
        <th scope="col" class="feed-week${starts.has(position) ? ' stage-start' : ''}"
            data-col="${position}" title="${escapeHtml(stageName(column.stage))} semana ${column.week}"
            >${column.week}</th>`).join('');

    const rows = planDraft.map((product, index) => {
        const name = escapeHtml(product.name);
        const cells = columns.map((column, position) => {
            const value = product.doses?.[column.stage]?.[column.week] ?? '';
            return `
            <td class="feed-cell${value ? ' has' : ''}${starts.has(position) ? ' stage-start' : ''}"
                data-col="${position}">
                <input type="number" min="0" step="0.5" inputmode="decimal" placeholder="—"
                       value="${value === '' ? '' : Number(value)}"
                       data-row="${index}" data-stage="${escapeHtml(column.stage)}"
                       data-week="${column.week}"
                       aria-label="${name} — ${escapeHtml(stageName(column.stage))} semana ${column.week}">
            </td>`;
        }).join('');

        return `
        <tr>
            <th scope="row" class="feed-rowh">
                <div class="feed-plan-line">
                    <input type="text" class="feed-plan-name" data-row="${index}"
                           value="${name}" aria-label="Nombre del producto">
                    <select class="feed-plan-unit" data-row="${index}"
                            aria-label="Unidad de ${name}">
                        ${UNITS.map(unit => `<option value="${unit}"
                            ${product.unit === unit ? 'selected' : ''}>${unit}</option>`).join('')}
                    </select>
                    <button class="ac-btn ac-btn--ghost ac-btn--icon feed-plan-del" data-row="${index}"
                            type="button" aria-label="Quitar ${name}" title="Quitar producto">${REMOVE_ICON}</button>
                </div>
            </th>
            ${cells}
        </tr>`;
    }).join('');

    table.innerHTML = `
        ${cols}
        <thead>
            <tr><th class="feed-rowh feed-rowh-head"></th>${stageRow}</tr>
            <tr><th scope="col" class="feed-rowh feed-rowh-head">Producto</th>${weekRow}</tr>
        </thead>
        <tbody>${rows}</tbody>`;

    if (!planDraft.length) {
        table.innerHTML += '<caption class="feed-plan-empty">No hay productos. Tocá «Agregar producto».</caption>';
    }
    markCurrentWeek();
}

/**
 * Mark the column the grow is in now, and say so in the caption above the grid.
 *
 * Separate from renderPlanEditor on purpose: the today poll runs while the
 * grid may hold unsaved edits, and re-rendering would throw them away. This
 * only moves two classes and a line of text.
 */
function markCurrentWeek() {
    const table = el('feed-plan-table');
    const caption = el('feed-plan-current');
    if (!table || !plan) return;

    const current = currentColumnIndex(plan.columns, today?.stage, today?.week);
    table.querySelectorAll('.is-current').forEach(node => node.classList.remove('is-current'));
    if (current >= 0) {
        table.querySelectorAll(`[data-col="${current}"]`)
            .forEach(node => node.classList.add('is-current'));
    }

    if (caption) {
        caption.hidden = current < 0;
        caption.textContent = current >= 0
            ? `Semana actual: ${stageName(plan.columns[current].stage)} · semana ${plan.columns[current].week}`
            : '';
    }
}

/**
 * Bring the current week into view. The product column is sticky and covers
 * the left edge, so the week lands just past it rather than under it.
 */
function scrollToCurrentWeek() {
    const scroller = document.querySelector('#feed-plan-editor .feed-plan-scroll');
    const marked = document.querySelector('#feed-plan-table .feed-week.is-current');
    const name = document.querySelector('#feed-plan-table .feed-rowh');
    if (!scroller || !marked || !name) return;

    const offset = marked.getBoundingClientRect().left
        - scroller.getBoundingClientRect().left;
    scroller.scrollLeft += offset - name.getBoundingClientRect().width - 8;
}

/** One delegated listener for the whole grid, re-rendered on every change. */
function wirePlanEditor() {
    const editor = el('feed-plan-editor');
    if (!editor) return;

    editor.addEventListener('input', event => {
        const target = event.target;
        const row = planDraft?.[Number(target.dataset.row)];
        if (!row) return;

        if (target.classList.contains('feed-plan-name')) {
            row.name = target.value;
            markDirty();
            return;
        }
        if (target.matches('.feed-cell input')) {
            const { stage, week } = target.dataset;
            row.doses = row.doses || {};
            row.doses[stage] = row.doses[stage] || {};
            const value = Number(target.value);
            if (value > 0) row.doses[stage][week] = value;
            else delete row.doses[stage][week];
            target.closest('.feed-cell').classList.toggle('has', value > 0);
            markDirty();
        }
    });

    editor.addEventListener('change', event => {
        if (!event.target.classList.contains('feed-plan-unit')) return;
        const row = planDraft?.[Number(event.target.dataset.row)];
        if (!row) return;
        row.unit = event.target.value;
        markDirty();
    });

    editor.addEventListener('click', event => {
        const remove = event.target.closest('.feed-plan-del');
        if (!remove) return;
        planDraft.splice(Number(remove.dataset.row), 1);
        renderPlanEditor();
        markDirty();
    });
}

/**
 * Switch between today's form and the weekly plan. The button that was pressed
 * says which one (`data-mode`); without one, it flips.
 */
function togglePlanEditor(trigger) {
    const editor = el('feed-plan-editor');
    const form = el('feed-form');
    if (!editor || !form) return;

    const mode = trigger?.dataset?.mode;
    const showPlan = mode ? mode === 'plan' : editor.hidden;
    // Re-pressing "Plan por semana" must not throw away unsaved grid edits.
    if (showPlan && !editor.hidden) return;

    editor.hidden = !showPlan;
    form.hidden = showPlan;
    // The plan needs the whole width of the view, so the CSS drops the calendar
    // column while it is on screen; the class is the switch.
    document.querySelector('.view[data-view="riego"]')?.classList.toggle('is-plan-mode', showPlan);
    el('feed-mode-today').setAttribute('aria-selected', String(!showPlan));
    el('feed-mode-plan').setAttribute('aria-selected', String(showPlan));
    if (showPlan) {
        planDraft = cloneplan();
        el('feed-plan-state').textContent = '';
        renderPlanEditor();
        scrollToCurrentWeek();
    }
}

function addPlanProduct() {
    if (!planDraft) planDraft = cloneplan();
    planDraft.push({ name: 'Producto nuevo', unit: 'ml/L', doses: {} });
    renderPlanEditor();
    markDirty();

    const names = document.querySelectorAll('#feed-plan-table .feed-plan-name');
    const last = names[names.length - 1];
    if (last) { last.focus(); last.select(); }
}

async function savePlan() {
    const state = el('feed-plan-state');
    state.textContent = 'Guardando…';
    try {
        const response = await fetch('/api/feed-plan', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ products: planDraft }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Error al guardar');

        state.textContent = 'Guardado';
        await fetchFeedPlan();
        planDraft = cloneplan();
        renderPlanEditor();
    } catch (error) {
        console.error('Error saving feed plan:', error);
        state.textContent = 'No se pudo guardar. Probá de nuevo.';
    }
}

// ---------------------------------------------------------------------------
// Network
// ---------------------------------------------------------------------------

async function fetchFeedPlan() {
    const data = await apiGet('/api/feed-plan');
    if (!data || !data.products) return;
    plan = data;
    renderForm();
    renderPlanEditor();
}

function fetchFeedingToday() {
    const litres = Number(el('feed-litres')?.value) || undefined;
    const url = litres ? `/api/feeding/today?litres=${litres}` : '/api/feeding/today';
    apiGet(url, { key: 'feeding-today' }).then(data => {
        if (!data || data.error) return;
        today = data;
        markCurrentWeek();

        // Only adopt the server's volume while the grower has not typed one.
        const field = el('feed-litres');
        if (field && !formTouched && Number.isFinite(data.litres)) {
            field.value = data.litres;
        }
        renderToday();
        if (!formTouched) renderForm();
        announce();
    });
}

function fetchIrrigations() {
    apiGet('/api/irrigations?limit=60', { key: 'irrigations' }).then(data => {
        if (!data || !data.irrigations) return;
        history = data.irrigations;
        renderHistory();
        announce();
    });
}

async function saveIrrigation() {
    const litres = Number(el('feed-litres').value);
    if (!(litres > 0)) {
        await alertDialog({
            title: 'Faltan los litros',
            message: 'Las dosis del plan son por litro, así que sin el volumen no '
                + 'hay nada que registrar.',
        });
        el('feed-litres').focus();
        return;
    }

    const button = document.querySelector('[data-action="feed-save"]');
    button.disabled = true;
    try {
        const response = await fetch('/api/irrigations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                litres,
                items: readForm(),
                ph: Number(el('feed-ph').value) || null,
                ec: Number(el('feed-ec').value) || null,
                notes: el('feed-notes').value.trim(),
            }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Error al registrar');

        el('feed-ph').value = '';
        el('feed-ec').value = '';
        el('feed-notes').value = '';
        formTouched = false;
        toast(`Riego de ${litres} L registrado`);
        fetchFeedingToday();
        fetchIrrigations();
    } catch (error) {
        console.error('Error recording irrigation:', error);
        await alertDialog({
            title: 'No se pudo registrar el riego',
            message: error.message,
        });
    } finally {
        button.disabled = false;
    }
}

/**
 * Delete a logged watering, after asking.
 *
 * This used to be two clicks on the same button, with the second one reading
 * "Confirmar borrado". It did not block the page, which was the point, but it
 * said nothing about *what* was being deleted, and a list where one row is
 * silently armed is a list where the poll that re-renders it disarms the row
 * under your finger. A dialog that names the date is both safer and clearer.
 */
async function deleteIrrigation(irrigationId, date) {
    const confirmed = await confirmDialog({
        title: 'Borrar riego',
        message: `Se borra el riego del ${date} y todo lo que se anotó que llevaba. `
            + 'No se puede deshacer.',
        confirmLabel: 'Borrar riego',
    });
    if (!confirmed) return;

    try {
        const response = await fetch(`/api/irrigations/${irrigationId}`, { method: 'DELETE' });
        if (!response.ok) throw new Error('No se pudo borrar');
        toast('Riego borrado');
        fetchIrrigations();
    } catch (error) {
        console.error('Error deleting irrigation:', error);
        await alertDialog({
            title: 'No se pudo borrar el riego',
            message: 'El backend rechazó el pedido o no contestó.',
        });
    }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function resetFormToPlan() {
    formTouched = false;
    if (today && Number.isFinite(today.litres)) el('feed-litres').value = today.litres;
    fetchFeedingToday();
    renderForm();
}

function wireFeeding() {
    const litres = el('feed-litres');
    if (!litres) return;

    litres.addEventListener('input', () => {
        formTouched = true;
        renderToday();
        refreshTotals();
    });

    const products = el('feed-products');
    products.addEventListener('input', () => { formTouched = true; refreshTotals(); });
    products.addEventListener('change', () => { formTouched = true; refreshTotals(); });

    el('feed-history').addEventListener('click', event => {
        const button = event.target.closest('.feed-delete');
        if (button) {
            deleteIrrigation(Number(button.dataset.irrigationId),
                             button.dataset.irrigationDate);
        }
    });

    wirePlanEditor();
}

function fetchFeeding() {
    fetchFeedingToday();
    fetchIrrigations();
}

export {
    addPlanProduct, fetchFeedPlan, fetchFeeding, fetchFeedingToday, fetchIrrigations,
    resetFormToPlan, saveIrrigation, savePlan, togglePlanEditor, wireFeeding,
};
