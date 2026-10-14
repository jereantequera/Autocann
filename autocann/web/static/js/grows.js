import { apiGet } from './api.js';
import { closeModal, showModal } from './modals.js';
import { getVpdChart, renderStageMarkers } from './charts.js';
import { fetchDatabaseStats, fetchHistoricalData, fetchPeriodSummary } from './dashboard.js';
import { fetchVpdScore } from './analytics.js';
import { config, state } from './state.js';
import { escapeHtml } from './util.js';

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
    document.getElementById('newGrowStage').value = 'early_veg';
    document.getElementById('newGrowNotes').value = '';
    showModal('newGrowModal');
}

function showStageModal() {
    showModal('stageModal');
}

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

/** Stage history for the active grow: the chart markers and the Cultivo list. */
function fetchStageTimeline(growId) {
    if (!Number.isFinite(growId)) return;
    apiGet(`/api/grows/${growId}/timeline`, { key: 'timeline' }).then(data => {
        if (!data || !data.timeline) return;
        state.timeline = data.timeline;
        renderStageMarkers(data.timeline);
        renderStageTimeline(data.timeline);
    });
}

/**
 * Each stage with the days it lasted next to the days it was meant to.
 *
 * This was already being fetched to draw dashed lines on the charts. Written
 * out it answers the question the markers only hint at: is this grow running
 * long, and which stage is where it went.
 */
function renderStageTimeline(timeline) {
    const box = document.getElementById('grow-timeline');
    if (!box) return;

    if (!timeline || !timeline.length) {
        box.innerHTML = '<div class="feed-empty">Este cultivo todavía no '
            + 'registró ningún cambio de etapa.</div>';
        return;
    }

    box.innerHTML = timeline.map(period => {
        const expected = Number.isFinite(period.expected_days) ? period.expected_days : null;
        const days = Number.isFinite(period.days) ? period.days : null;
        const overdue = days !== null && expected !== null && days > expected;
        const pct = days !== null && expected
            ? Math.min((days / expected) * 100, 100) : 0;

        const span = period.ended_date
            ? `${period.started_date} → ${period.ended_date}`
            : `${period.started_date} → en curso`;

        return `
            <div class="timeline-item ${escapeHtml(period.stage)}${period.is_current ? ' is-current' : ''}">
                <div class="timeline-head">
                    <span class="grow-stage ${escapeHtml(period.stage)}">
                        ${escapeHtml(config.stageNames[period.stage] || period.stage)}
                    </span>
                    <span class="timeline-days${overdue ? ' overdue' : ''}">
                        ${days !== null ? `${days} d` : '--'}${expected ? ` de ~${expected}` : ''}
                    </span>
                </div>
                <div class="timeline-bar">
                    <div class="timeline-fill${overdue ? ' overdue' : ''}" style="width: ${pct}%"></div>
                </div>
                <div class="timeline-span">${escapeHtml(span)}</div>
            </div>`;
    }).join('');
}

// Modal functions

async function createGrow() {
    const name = document.getElementById('newGrowName').value.trim();
    const stage = document.getElementById('newGrowStage').value;
    const notes = document.getElementById('newGrowNotes').value.trim();

    if (!name) {
        alert('Por favor ingresá un nombre para el cultivo');
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
            alert(`✅ ${result.message}`);
            closeModal('newGrowModal');
            fetchActiveGrow();
            fetchDatabaseStats();
            fetchHistoricalData(state.period, state.interval, state.hours);
        } else {
            alert(`❌ Error: ${result.error}`);
        }
    } catch (error) {
        console.error('Error creating grow:', error);
        alert('❌ Error al crear el cultivo');
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
            alert(`✅ ${result.message}`);
            closeModal('stageModal');
            fetchActiveGrow();
        } else {
            alert(`❌ Error: ${result.error}`);
        }
    } catch (error) {
        console.error('Error updating stage:', error);
        alert('❌ Error al actualizar la etapa');
    }
}

async function activateGrow(growId) {
    if (!confirm('¿Querés activar este cultivo?')) return;

    try {
        const response = await fetch(`/api/grows/${growId}/activate`, { method: 'POST' });
        const result = await response.json();

        if (response.ok) {
            alert(`✅ ${result.message}`);
            loadGrowsList();
            fetchActiveGrow();
            fetchDatabaseStats();
            fetchHistoricalData(state.period, state.interval, state.hours);
        } else {
            alert(`❌ Error: ${result.error}`);
        }
    } catch (error) {
        console.error('Error activating grow:', error);
        alert('❌ Error al activar el cultivo');
    }
}

async function endGrow(growId) {
    if (!confirm('¿Querés finalizar este cultivo?')) return;

    try {
        const response = await fetch(`/api/grows/${growId}/end`, { method: 'POST' });
        const result = await response.json();

        if (response.ok) {
            alert(`✅ ${result.message}`);
            loadGrowsList();
        } else {
            alert(`❌ Error: ${result.error}`);
        }
    } catch (error) {
        console.error('Error ending grow:', error);
        alert('❌ Error al finalizar el cultivo');
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
                badge.className = 'sensor-badge';
                badge.textContent = `${data.grows.length} `
                    + `${data.grows.length === 1 ? 'cultivo' : 'cultivos'}`;
            }

            // Every interpolated value is escaped: names, dates and the
            // stage come straight from the database.
            growListElement.innerHTML = data.grows.map(grow => {
                const id = Number(grow.id);
                const stage = escapeHtml(grow.stage);
                const samples = Number.isFinite(grow.sample_count)
                    ? `• ${grow.sample_count.toLocaleString('es-AR')} muestras` : '';
                return `
                <div class="grow-item ${grow.is_active ? 'active' : ''}"
                     data-grow-id="${id}"
                     data-action="${grow.is_active ? '' : 'activate'}">
                    <div class="grow-item-name">
                        ${escapeHtml(grow.name)}
                        ${grow.is_active ? '<span style="color: var(--accent-green);">✅ ACTIVO</span>' : ''}
                    </div>
                    <span class="grow-stage ${stage}">${escapeHtml(stageNames[grow.stage] || grow.stage)}</span>
                    <div class="grow-item-date">
                        Inicio: ${escapeHtml(grow.start_date)}<br>
                        ${grow.end_date ? `Fin: ${escapeHtml(grow.end_date)}` : ''}
                        ${samples}
                    </div>
                    ${!grow.end_date && !grow.is_active ? `
                        <button class="btn btn-ghost" data-action="end" data-grow-id="${id}"
                                style="margin-top: 10px; color: #ef4444; border-color: #ef4444;">
                            Finalizar
                        </button>
                    ` : ''}
                </div>`;
            }).join('');
        } else {
            if (badge) { badge.className = 'sensor-badge'; badge.textContent = 'ninguno'; }
            growListElement.innerHTML =
                '<div class="feed-empty">No hay cultivos registrados.</div>';
        }
    } catch (error) {
        console.error('Error loading grows:', error);
        growListElement.innerHTML =
            '<div class="feed-empty is-error">No se pudieron cargar los cultivos.</div>';
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
