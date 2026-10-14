import { apiGet } from './api.js';
import { alertDialog, confirmDialog, toast } from './modals.js';
import { config } from './state.js';
import { doseAmount, escapeHtml, fmtDose, lastWateringLabel } from './util.js';

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

function renderToday() {
    const doses = el('feed-doses');
    const week = el('feed-week');
    if (!doses) return;

    if (!today) {
        doses.innerHTML = '<div class="loading">Cargando plan</div>';
        return;
    }

    week.textContent = today.week
        ? `${stageName(today.stage)} · semana ${today.week} de ${today.weeks_in_stage}`
        : stageName(today.stage);

    if (today.flush) {
        doses.innerHTML = `
            <div class="feed-flush">
                Semana de lavado: el plan no lleva nutrientes.
                Regá con agua sola y el pH corregido.
            </div>`;
        return;
    }
    if (!today.items.length) {
        doses.innerHTML = `
            <div class="feed-flush">
                No hay nada cargado en el plan para esta semana.
                Abrí «Plan» y completá la grilla.
            </div>`;
        return;
    }

    const litres = Number(el('feed-litres').value) || today.litres;
    doses.innerHTML = today.items.map(item => `
        <div class="feed-dose-row">
            <span class="feed-dose-name">${escapeHtml(item.name)}</span>
            <span class="feed-dose-amount">
                ${escapeHtml(doseAmount(item, litres))}
                <small>${escapeHtml(fmtDose(item.dose))} ${escapeHtml(item.unit)}</small>
            </span>
        </div>`).join('');
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

    box.innerHTML = ordered.map(product => {
        const planned = due.get(product.id);
        const id = Number(product.id);
        return `
        <label class="feed-product${planned ? ' on' : ''}" data-product-id="${id}">
            <input type="checkbox" ${planned ? 'checked' : ''}
                   aria-label="${escapeHtml(product.name)}">
            <span class="feed-product-name">
                ${escapeHtml(product.name)}
                <small>${planned ? `plan ${escapeHtml(fmtDose(planned))} ${escapeHtml(product.unit)}`
                                 : escapeHtml(product.unit)}</small>
            </span>
            <input type="number" class="feed-product-dose" min="0" step="0.5"
                   inputmode="decimal" value="${planned ? fmtDose(planned).replace(',', '.') : ''}"
                   aria-label="Dosis de ${escapeHtml(product.name)}">
            <span class="feed-product-total"></span>
        </label>`;
    }).join('');

    refreshTotals();
}

function refreshTotals() {
    const litres = Number(el('feed-litres').value) || 0;
    document.querySelectorAll('#feed-products .feed-product').forEach(row => {
        const checkbox = row.querySelector('input[type=checkbox]');
        const input = row.querySelector('.feed-product-dose');
        const total = row.querySelector('.feed-product-total');
        const value = Number(input.value) || 0;
        const active = checkbox.checked && value > 0;
        const product = plan.products.find(p => p.id === Number(row.dataset.productId));

        row.classList.toggle('on', active);
        total.textContent = active && product
            ? doseAmount({ dose: value, unit: product.unit }, litres)
            : '--';
    });
}

function readForm() {
    const planned = new Set((today?.items || []).map(item => item.product_id));
    const items = [];
    document.querySelectorAll('#feed-products .feed-product').forEach(row => {
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

function renderHistory() {
    const box = el('feed-history');
    const badge = el('feed-last-badge');
    if (!box) return;

    if (!history.length) {
        box.innerHTML = `
            <div class="feed-empty">
                Todavía no hay riegos registrados para este cultivo.
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
            <span class="feed-pill${item.on_plan ? '' : ' off'}">
                ${escapeHtml(item.name)}
                ${escapeHtml(fmtDose(item.dose))} ${escapeHtml(item.unit)}
                · ${escapeHtml(doseAmount(item, entry.litres))}
            </span>`).join('') || '<span class="feed-pill">Solo agua</span>';

        const measures = [
            entry.stage ? `${escapeHtml(stageName(entry.stage))} S${Number(entry.week) || '?'}` : '',
            Number.isFinite(entry.ph) ? `pH ${escapeHtml(fmtDose(entry.ph))}` : '',
            Number.isFinite(entry.ec) ? `EC ${escapeHtml(fmtDose(entry.ec))}` : '',
        ].filter(Boolean).map(text => `<span class="feed-tag">${text}</span>`).join('');

        return `
        <div class="feed-entry">
            <div class="feed-entry-head">
                <span class="feed-entry-date">${escapeHtml(entry.date)}</span>
                <span class="feed-entry-litres">${escapeHtml(fmtDose(entry.litres))} L</span>
            </div>
            <div class="feed-entry-tags">${measures}</div>
            <div class="feed-entry-items">${items}</div>
            ${entry.notes ? `<div class="feed-entry-note">${escapeHtml(entry.notes)}</div>` : ''}
            <div class="feed-entry-foot">
                <button class="btn btn-ghost btn-mini feed-delete" data-irrigation-id="${id}"
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
    const header = columns.map((column, index) => {
        const first = index === 0 || columns[index - 1].stage !== column.stage;
        const initial = column.stage === 'early_veg' ? 'V'
            : column.stage === 'late_veg' ? 'L'
            : column.stage === 'flowering' ? 'F' : 'S';
        return `<th class="feed-col ${escapeHtml(column.stage)}${first ? ' stage-start' : ''}"
                    title="${escapeHtml(stageName(column.stage))} semana ${column.week}"
                >${initial}${column.week}</th>`;
    }).join('');

    const rows = planDraft.map((product, index) => {
        const cells = columns.map((column, position) => {
            const value = product.doses?.[column.stage]?.[column.week] ?? '';
            const first = position === 0 || columns[position - 1].stage !== column.stage;
            return `
            <td class="feed-cell${value ? ' has' : ''}${first ? ' stage-start' : ''}">
                <input type="number" min="0" step="0.5" inputmode="decimal"
                       value="${value === '' ? '' : Number(value)}"
                       data-row="${index}" data-stage="${escapeHtml(column.stage)}"
                       data-week="${column.week}"
                       aria-label="${escapeHtml(product.name)} — ${escapeHtml(stageName(column.stage))} semana ${column.week}">
            </td>`;
        }).join('');

        return `
        <tr>
            <th class="feed-rowh">
                <input type="text" class="feed-plan-name" data-row="${index}"
                       value="${escapeHtml(product.name)}" aria-label="Nombre del producto">
                <select class="feed-plan-unit" data-row="${index}"
                        aria-label="Unidad de ${escapeHtml(product.name)}">
                    ${UNITS.map(unit => `<option value="${unit}"
                        ${product.unit === unit ? 'selected' : ''}>${unit}</option>`).join('')}
                </select>
                <button class="feed-plan-del" data-row="${index}" type="button"
                        aria-label="Quitar ${escapeHtml(product.name)}">×</button>
            </th>
            ${cells}
        </tr>`;
    }).join('');

    table.innerHTML = `
        <thead><tr><th class="feed-rowh">Producto</th>${header}</tr></thead>
        <tbody>${rows}</tbody>`;

    if (!planDraft.length) {
        table.innerHTML += '<caption class="feed-plan-empty">No hay productos. Tocá «Agregar producto».</caption>';
    }
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

function togglePlanEditor() {
    const editor = el('feed-plan-editor');
    const toggle = el('feed-plan-toggle');
    if (!editor) return;

    editor.hidden = !editor.hidden;
    toggle.textContent = editor.hidden ? 'Plan' : 'Cerrar';
    if (!editor.hidden) {
        planDraft = cloneplan();
        el('feed-plan-state').textContent = '';
        renderPlanEditor();
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
