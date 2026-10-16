import { apiGet } from './api.js';
import { alertDialog, closeModal, confirmDialog, showModal, toast } from './modals.js';
import { getVpdChart, renderStageMarkers } from './charts.js';
import { fetchDatabaseStats, fetchHistoricalData, fetchPeriodSummary } from './dashboard.js';
import { fetchVpdScore } from './analytics.js';
import { fetchHealth } from './health.js';
import { config, state } from './state.js';
import { escapeHtml, icon } from './util.js';
import { paintTent } from './sidebar.js';
import {
    durationLabel, formatLongDate, formatShortDate, growDays, growLine, nextStage,
    stageBarModel,
} from './grow-model.js';

/**
 * Grow lifecycle: the active grow, its stage history, and the list of all of
 * them.
 *
 * The list and the stage history live in the Cultivo view. Creating a grow and
 * changing a stage are still modals, which is what a dialog is for: a short
 * form, answered and dismissed.
 */

function showNewGrowModal() {
    document.getElementById('newGrowName').value = '';
    document.getElementById('newGrowStage').value = 'seedling';
    document.getElementById('newGrowNotes').value = '';
    showModal('newGrowModal');
}

/**
 * Open the stage dialog, preselected: on the next stage when the primary button
 * asked for it, otherwise on the current one.
 */
function showStageModal(trigger) {
    const select = document.getElementById('newStage');
    const wanted = trigger?.dataset?.stage || state.stage;
    if (select && wanted && [...select.options].some(option => option.value === wanted)) {
        select.value = wanted;
    }
    showModal('stageModal');
}

/** The stage order and expected lengths the server defines; fetched once. */
let stageConfig = null;
let stageConfigRequest = null;

function loadStageConfig() {
    if (stageConfig) return Promise.resolve(stageConfig);
    if (!stageConfigRequest) {
        stageConfigRequest = apiGet('/api/config').then(data => {
            if (!data || !Array.isArray(data.stages)) {
                // Not cached on failure: the next render asks again.
                stageConfigRequest = null;
                return null;
            }
            stageConfig = {
                order: data.stages,
                expected: data.stage_expected_days || null,
            };
            return stageConfig;
        });
    }
    return stageConfigRequest;
}

/** The active grow and its stage history, as the Cultivo header and bar draw them. */
let activeGrow = null;
let timelineData = null;

// Grow management

function updateActiveGrow(grow) {
    if (!grow) return;
    
    document.getElementById('grow-name').textContent = grow.name;
    const detailName = document.getElementById('grow-detail-name');
    if (detailName) detailName.textContent = grow.name;
    state.stage = grow.stage;
    
    const stageElement = document.getElementById('grow-stage');
    const stageNames = config.stageNames;
    
    stageElement.textContent = stageNames[grow.stage] || grow.stage;
    stageElement.className = `grow-stage ${grow.stage}`;
    
    renderDayCounters(grow);
    // The collapsed bar shows the tent as a monogram, and its title and
    // accessible name carry the same words as the expanded block.
    paintTent({
        name: grow.name,
        stage: stageNames[grow.stage] || grow.stage,
        day: grow.day_in_stage,
        expected: grow.stage_expected_days,
    });
    activeGrow = grow;
    renderCultivo();

    // Update VPD chart optimal zone
    const vpdChart = getVpdChart();
    if (vpdChart && config.vpdRanges[state.stage]) {
        const range = config.vpdRanges[state.stage];
        vpdChart.options.plugins.annotation.annotations.optimalZone.yMin = range.min;
        vpdChart.options.plugins.annotation.annotations.optimalZone.yMax = range.max;
        vpdChart.update();
    }
}

/**
 * The Cultivo header: which grow, since when, where it is in its stage, and
 * what the primary button will do next.
 */
function renderCultivo() {
    const grow = activeGrow;
    if (!grow) return;

    const kicker = document.getElementById('grow-since');
    const line = document.getElementById('grow-line');
    if (kicker) kicker.textContent = `Cultivo activo · desde el ${formatLongDate(grow.start_date)}`;
    if (line) {
        line.textContent = growLine({
            stageName: config.stageNames[grow.stage] || grow.stage,
            day: grow.day_in_stage,
            expected: grow.stage_expected_days,
            vpd: config.vpdRanges[grow.stage],
        });
    }

    // The stage order arrives with /api/config; until then the bar and the
    // next-stage button have nothing to draw from.
    loadStageConfig().then(stages => {
        if (!stages) return;
        const next = nextStage(stages.order, grow.stage);
        const button = document.getElementById('grow-next-stage');
        if (button) {
            button.hidden = !next;
            if (next) {
                button.dataset.stage = next;
                const name = config.stageNames[next] || next;
                button.textContent = `Pasar a ${name.toLocaleLowerCase('es-AR')}`;
            }
        }
        drawStageBar();
    });
}

/**
 * "Floración · día 12 de ~56" plus the progress bar.
 *
 * Days are calendar days counted by the server; the start date is day 1.
 */
function renderDayCounters(grow) {
    const stageDay = document.getElementById('stage-day');
    const growDay = document.getElementById('grow-day');
    const progress = document.getElementById('stage-progress');
    const fill = document.getElementById('stage-progress-fill');
    const label = document.getElementById('stage-progress-label');
    if (!stageDay) return;

    const day = grow.day_in_stage;
    const expected = grow.stage_expected_days;

    if (!Number.isFinite(day)) {
        // A grow with no recorded history: say nothing rather than show "día --".
        stageDay.textContent = '';
        growDay.textContent = '';
        progress.hidden = true;
        return;
    }

    const overdue = Number.isFinite(expected) && day > expected;
    stageDay.textContent = expected ? `día ${day} de ~${expected}` : `día ${day}`;
    stageDay.classList.toggle('overdue', overdue);
    stageDay.title = overdue
        ? `Lleva ${day - expected} día(s) más que la duración típica de la etapa`
        : 'Día dentro de la etapa actual';

    growDay.textContent = Number.isFinite(grow.day_of_grow)
        ? `cultivo: día ${grow.day_of_grow}` : '';

    if (!Number.isFinite(expected)) {
        progress.hidden = true;
        return;
    }

    progress.hidden = false;
    const pct = Math.min((day / expected) * 100, 100);
    fill.style.width = `${pct}%`;
    fill.classList.toggle('overdue', overdue);

    if (Number.isFinite(grow.estimated_end)) {
        const end = new Date(grow.estimated_end * 1000);
        label.textContent = `fin estimado ${end.toLocaleDateString('es-AR', { day: '2-digit', month: 'short' })}`;
        label.title = 'Estimación: asume que las etapas restantes duran lo típico';
    } else {
        label.textContent = `${Math.round(pct)}%`;
        label.title = '';
    }
}

function fetchActiveGrow() {
    apiGet('/api/grows/active').then(data => {
        if (!data) return;
        updateActiveGrow(data);
        fetchStageTimeline(data.id);
    });
}

/** Stage history for the active grow: the chart markers and the stage bar. */
function fetchStageTimeline(growId) {
    if (!Number.isFinite(growId)) return;
    apiGet(`/api/grows/${growId}/timeline`, { key: 'timeline' }).then(data => {
        if (!data || !data.timeline) return;
        state.timeline = data.timeline;
        timelineData = data.timeline;
        renderStageMarkers(data.timeline);
        drawStageBar();
    });
}

/**
 * The stage bar: every stage of the cycle, its real days where the grow went
 * through it, and how far the current one has got.
 *
 * Drawn from whatever has arrived; it redraws when the timeline or the stage
 * order lands, so a late answer only fills in cells that were waiting.
 */
function drawStageBar() {
    const box = document.getElementById('grow-timeline');
    if (!box || !activeGrow || !stageConfig) return;

    const expected = { ...(stageConfig.expected || {}) };
    if (Number.isFinite(activeGrow.stage_expected_days)) {
        expected[activeGrow.stage] = activeGrow.stage_expected_days;
    }
    const cells = stageBarModel({
        order: stageConfig.order,
        current: activeGrow.stage,
        dayInStage: activeGrow.day_in_stage,
        expected,
        timeline: timelineData,
        names: config.stageNames,
    });

    box.style.gridTemplateColumns = cells.map(cell => `${cell.flex}fr`).join(' ');
    box.innerHTML = cells.map(cell => `
        <div class="ac-stage is-${cell.state}">
            <div class="ac-stage__bar"><div class="ac-stage__fill" style="width: ${cell.fill.toFixed(1)}%"></div></div>
            <div class="ac-stage__name">${escapeHtml(cell.name)}</div>
            <div class="ac-stage__meta">${escapeHtml(cell.meta)}</div>
        </div>`).join('');
}

// Modal functions

async function createGrow() {
    const field = document.getElementById('newGrowName');
    const name = field.value.trim();
    const stage = document.getElementById('newGrowStage').value;
    const notes = document.getElementById('newGrowNotes').value.trim();

    if (!name) {
        await alertDialog({
            title: 'Falta el nombre',
            message: 'El cultivo necesita un nombre para poder distinguirlo del '
                + 'anterior en los gráficos.',
        });
        // Back to the field it is complaining about, not to wherever focus
        // happened to be.
        field.focus();
        return;
    }

    try {
        const response = await fetch('/api/grows', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, stage, notes })
        });

        const result = await response.json();

        if (response.ok) {
            closeModal('newGrowModal');
            toast(`Cultivo "${name}" creado`);
            // The new grow is the active one, so every chart on screen is now
            // about it.
            state.growId = null;
            fetchActiveGrow();
            fetchDatabaseStats();
            fetchGrowFilter();
            fetchHistoricalData(state.period, state.interval, state.hours);
            // The "no hay cultivo activo" notice goes now, not ten seconds later.
            fetchHealth();
        } else {
            await alertDialog({
                title: 'No se pudo crear el cultivo',
                message: result.error || 'El backend rechazó el pedido.',
            });
        }
    } catch (error) {
        console.error('Error creating grow:', error);
        await alertDialog({
            title: 'No se pudo crear el cultivo',
            message: 'No se pudo contactar al backend.',
        });
    }
}

async function updateStage() {
    const newStage = document.getElementById('newStage').value;

    try {
        const activeGrow = await fetch('/api/grows/active').then(r => r.json());

        const response = await fetch(`/api/grows/${activeGrow.id}/stage`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stage: newStage })
        });

        const result = await response.json();

        if (response.ok) {
            closeModal('stageModal');
            toast(`Etapa: ${config.stageNames[newStage] || newStage}`);
            fetchActiveGrow();
        } else {
            await alertDialog({
                title: 'No se pudo cambiar la etapa',
                message: result.error || 'El backend rechazó el pedido.',
            });
        }
    } catch (error) {
        console.error('Error updating stage:', error);
        await alertDialog({
            title: 'No se pudo cambiar la etapa',
            message: 'No se pudo contactar al backend.',
        });
    }
}

/** The grow's name, for a dialog that has to say which one it is about. */
function growName(growId) {
    const row = document.querySelector(`#growList tr[data-grow-id="${growId}"]`);
    return row?.querySelector('.grow-name-cell')?.textContent.trim() || 'este cultivo';
}

async function activateGrow(growId) {
    const confirmed = await confirmDialog({
        title: 'Activar cultivo',
        message: `El tablero entero pasa a hablar de "${growName(growId)}": los `
            + 'gráficos, las estadísticas y el plan de riego. El lazo de control '
            + 'toma su etapa en menos de un minuto.',
        confirmLabel: 'Activar cultivo',
        // Not destructive: nothing is lost, and painting it red next to
        // "Finalizar cultivo" would make both reds mean less.
        danger: false,
    });
    if (!confirmed) return;

    try {
        const response = await fetch(`/api/grows/${growId}/activate`, { method: 'POST' });
        const result = await response.json();

        if (response.ok) {
            toast('Cultivo activado');
            state.growId = null;
            loadGrowsList();
            fetchActiveGrow();
            fetchDatabaseStats();
            fetchGrowFilter();
            fetchHistoricalData(state.period, state.interval, state.hours);
        } else {
            await alertDialog({
                title: 'No se pudo activar el cultivo',
                message: result.error || 'El backend rechazó el pedido.',
            });
        }
    } catch (error) {
        console.error('Error activating grow:', error);
        await alertDialog({
            title: 'No se pudo activar el cultivo',
            message: 'No se pudo contactar al backend.',
        });
    }
}

async function endGrow(growId) {
    const confirmed = await confirmDialog({
        title: 'Finalizar cultivo',
        message: `"${growName(growId)}" queda cerrado con la fecha de hoy. El `
            + 'histórico no se borra y se puede seguir mirando eligiéndolo en '
            + 'Estadísticas, pero el cultivo no se puede reabrir.',
        confirmLabel: 'Finalizar cultivo',
    });
    if (!confirmed) return;

    try {
        const response = await fetch(`/api/grows/${growId}/end`, { method: 'POST' });
        const result = await response.json();

        if (response.ok) {
            toast('Cultivo finalizado');
            loadGrowsList();
            fetchGrowFilter();
        } else {
            await alertDialog({
                title: 'No se pudo finalizar el cultivo',
                message: result.error || 'El backend rechazó el pedido.',
            });
        }
    } catch (error) {
        console.error('Error ending grow:', error);
        await alertDialog({
            title: 'No se pudo finalizar el cultivo',
            message: 'No se pudo contactar al backend.',
        });
    }
}

async function loadGrowsList() {
    const growListElement = document.getElementById('growList');
    growListElement.innerHTML = '<div class="loading">Cargando cultivos</div>';

    try {
        const response = await fetch('/api/grows');
        const data = await response.json();

        const badge = document.getElementById('grow-count-badge');

        if (data.grows && data.grows.length > 0) {
            // The names come from /api/config like everywhere else; this used to
            // keep a fourth copy of the same four strings.
            const stageNames = config.stageNames;

            if (badge) {
                badge.textContent = `${data.grows.length} `
                    + `${data.grows.length === 1 ? 'cultivo' : 'cultivos'}`;
            }

            // Every interpolated value is escaped: names, dates and the
            // stage come straight from the database. Riegos is not in the
            // grow list, so the table has no column for it.
            const rows = data.grows.map(grow => {
                const id = Number(grow.id);
                const samples = Number.isFinite(grow.sample_count)
                    ? grow.sample_count.toLocaleString('es-AR') : '--';
                const days = growDays(grow.start_date, grow.end_date);
                // The row is the same control as its "Activar" button, so a
                // click anywhere on an inactive grow still activates it.
                return `
                <tr class="${grow.is_active ? 'is-active' : ''}"
                    data-grow-id="${id}"
                    ${grow.is_active ? '' : 'data-action="activate"'}>
                    <td>
                        <span class="grow-name-cell">
                            <span class="ac-dot ${grow.is_active ? 'ac-dot--ok' : 'ac-dot--idle'}"></span>
                            ${escapeHtml(grow.name)}
                        </span>
                    </td>
                    <td>${escapeHtml(stageNames[grow.stage] || grow.stage)}</td>
                    <td class="ac-num">${escapeHtml(formatShortDate(grow.start_date))}</td>
                    <td class="ac-num">${escapeHtml(durationLabel(days))}</td>
                    <td class="ac-num">${escapeHtml(samples)}</td>
                    <td class="grow-row-actions">
                        ${grow.is_active
                            ? '<span class="ac-field__hint">activo</span>'
                            : `<button class="ac-btn ac-btn--ghost" data-action="activate"
                                       data-grow-id="${id}">Activar</button>`}
                        ${!grow.end_date && !grow.is_active ? `
                            <button class="ac-btn ac-btn--ghost grow-end" data-action="end"
                                    data-grow-id="${id}">Finalizar</button>
                        ` : ''}
                    </td>
                </tr>`;
            }).join('');

            growListElement.innerHTML = `
                <table class="ac-table grow-table">
                    <thead>
                        <tr>
                            <th>Nombre</th><th>Etapa</th><th>Inicio</th>
                            <th>Duración</th><th>Muestras</th><th></th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>`;
        } else {
            if (badge) badge.textContent = 'ninguno';
            growListElement.innerHTML =
                '<div class="ac-empty">No hay cultivos registrados.</div>';
        }
    } catch (error) {
        console.error('Error loading grows:', error);
        growListElement.innerHTML =
            '<div class="ac-empty">No se pudieron cargar los cultivos.</div>';
    }
}

// ---------------------------------------------------------------------------
// The grow scope selector in Estadísticas
// ---------------------------------------------------------------------------
// The history is filtered by grow, not only by time. A 30- or 90-day window
// used to reach back past the end of the previous crop and draw ten months of
// an empty tent and a real grow as one continuous curve.
//
// The default is the active grow — everything else on screen is about that
// grow, from the stage to the day counter — and "todos los cultivos" is still
// there, as something you pick.

const ALL_GROWS_OPTION = 'all';

/** Fill the selector from the grow list, keeping whatever is selected. */
function renderGrowFilter(grows) {
    const select = document.getElementById('grow-filter');
    if (!select) return;

    const options = [
        '<option value="">Cultivo activo</option>',
        ...(grows || []).map(grow => {
            const id = Number(grow.id);
            const samples = Number.isFinite(grow.sample_count)
                ? ` · ${grow.sample_count.toLocaleString('es-AR')} muestras` : '';
            return `<option value="${id}">${escapeHtml(grow.name)}`
                + `${grow.is_active ? ' (activo)' : ''}${samples}</option>`;
        }),
        `<option value="${ALL_GROWS_OPTION}">Todos los cultivos</option>`,
    ];

    select.innerHTML = options.join('');
    // Re-selecting after innerHTML: the list is refetched on every visit, and
    // losing the choice would quietly take the charts back to the active grow.
    select.value = state.growId === null ? '' : String(state.growId);
    if (select.value !== (state.growId === null ? '' : String(state.growId))) {
        // The chosen grow is gone from the list; fall back rather than show a
        // selector that disagrees with the charts.
        state.growId = null;
        select.value = '';
    }
}

function fetchGrowFilter() {
    apiGet('/api/grows', { key: 'grow-filter' }).then(data => {
        if (data && data.grows) renderGrowFilter(data.grows);
    });
}

function wireGrowFilter() {
    const select = document.getElementById('grow-filter');
    if (!select) return;

    select.addEventListener('change', () => {
        const value = select.value;
        state.growId = value === '' ? null
            : value === ALL_GROWS_OPTION ? ALL_GROWS_OPTION : Number(value);
        // Everything in this view reads the same scope, so all of it is
        // refetched together: two charts disagreeing about which grow they are
        // drawing is the exact confusion this selector exists to end.
        fetchHistoricalData(state.period, state.interval, state.hours);
        fetchPeriodSummary();
        fetchVpdScore();
    });

    document.addEventListener('view:enter', event => {
        if (event.detail?.view === 'stats') fetchGrowFilter();
    });
    fetchGrowFilter();
}

function wireGrowListActions() {
    const list = document.getElementById('growList');
    if (!list) return;

    // Loaded on arrival rather than polled: a grow is created a handful of
    // times a year, and the bar at the top already tracks the active one.
    document.addEventListener('view:enter', event => {
        if (event.detail?.view === 'cultivo') loadGrowsList();
    });

    list.addEventListener('click', event => {
        const endBtn = event.target.closest('[data-action="end"]');
        if (endBtn) {
            event.stopPropagation();
            endGrow(Number(endBtn.dataset.growId));
            return;
        }
        const item = event.target.closest('[data-action="activate"]');
        if (item) activateGrow(Number(item.dataset.growId));
    });
}

export {
    fetchStageTimeline, showNewGrowModal, showStageModal, loadGrowsList, fetchActiveGrow,
    createGrow, updateStage, activateGrow, endGrow, wireGrowListActions,
    renderGrowFilter, wireGrowFilter,
};
