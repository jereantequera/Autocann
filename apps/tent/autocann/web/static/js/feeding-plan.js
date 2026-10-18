/**
 * Riego, the weekly plan: the grid of doses per product and week, the editor
 * that changes it, and the fetch and save of /api/feed-plan.
 */

import { apiGet } from './api.js';
import { escapeHtml } from './util.js';
import { currentColumnIndex, stageGroups, stageLabel } from './feed-grid.js';
import { el, feedState, stageName } from './feeding-state.js';
import { renderForm } from './feeding-form.js';

/** The working copy the plan editor edits. */
let planDraft = null;

const UNITS = ['ml/L', 'g/L'];

const REMOVE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" '
    + 'stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
const ADD_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" '
    + 'stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';

// ---------------------------------------------------------------------------
// The plan editor
// ---------------------------------------------------------------------------

function cloneplan() {
    return JSON.parse(JSON.stringify(feedState.plan?.products || []));
}

function markDirty() {
    const state = el('feed-plan-state');
    if (state) state.textContent = 'Cambios sin guardar';
}

function renderPlanEditor() {
    const table = el('feed-plan-table');
    if (!table || !feedState.plan) return;
    if (!planDraft) planDraft = cloneplan();

    const columns = feedState.plan.columns;
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
export function markCurrentWeek() {
    const table = el('feed-plan-table');
    const caption = el('feed-plan-current');
    if (!table || !feedState.plan) return;

    const current = currentColumnIndex(feedState.plan.columns, feedState.today?.stage, feedState.today?.week);
    table.querySelectorAll('.is-current').forEach(node => node.classList.remove('is-current'));
    if (current >= 0) {
        table.querySelectorAll(`[data-col="${current}"]`)
            .forEach(node => node.classList.add('is-current'));
    }

    if (caption) {
        caption.hidden = current < 0;
        caption.textContent = current >= 0
            ? `Semana actual: ${stageName(feedState.plan.columns[current].stage)} · semana ${feedState.plan.columns[current].week}`
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
export function wirePlanEditor() {
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
export function togglePlanEditor(trigger) {
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

export function addPlanProduct() {
    if (!planDraft) planDraft = cloneplan();
    planDraft.push({ name: 'Producto nuevo', unit: 'ml/L', doses: {} });
    renderPlanEditor();
    markDirty();

    const names = document.querySelectorAll('#feed-plan-table .feed-plan-name');
    const last = names[names.length - 1];
    if (last) { last.focus(); last.select(); }
}

export async function savePlan() {
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

export async function fetchFeedPlan() {
    const data = await apiGet('/api/feed-plan');
    if (!data || !data.products) return;
    feedState.plan = data;
    renderForm();
    renderPlanEditor();
}
