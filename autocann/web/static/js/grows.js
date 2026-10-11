import { apiGet } from './api.js';
import { closeModal, showModal } from './modals.js';
import { getVpdChart, renderStageMarkers } from './charts.js';
import { fetchDatabaseStats, fetchHistoricalData } from './dashboard.js';
import { config, state } from './state.js';
import { escapeHtml } from './util.js';

/** Grow lifecycle: active grow, stage changes, and the grow list modal. */

function showNewGrowModal() {
    document.getElementById('newGrowName').value = '';
    document.getElementById('newGrowStage').value = 'early_veg';
    document.getElementById('newGrowNotes').value = '';
    showModal('newGrowModal');
}

function showStageModal() {
    showModal('stageModal');
}

function showGrowModal() {
    showModal('growsModal');
    loadGrowsList();
}

// Grow management

function updateActiveGrow(grow) {
    if (!grow) return;
    
    document.getElementById('grow-name').textContent = grow.name;
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

/** Stage history for the active grow; drives the markers on the charts. */
function fetchStageTimeline(growId) {
    if (!Number.isFinite(growId)) return;
    apiGet(`/api/grows/${growId}/timeline`, { key: 'timeline' }).then(data => {
        if (!data || !data.timeline) return;
        state.timeline = data.timeline;
        renderStageMarkers(data.timeline);
    });
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
            closeModal('growsModal');
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

        if (data.grows && data.grows.length > 0) {
            const stageNames = {
                'early_veg': 'Vegetativo Temprano',
                'late_veg': 'Vegetativo Tardío',
                'flowering': 'Floración',
                'dry': 'Secado'
            };

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
            growListElement.innerHTML = '<p style="text-align: center; color: var(--text-muted);">No hay cultivos registrados</p>';
        }
    } catch (error) {
        console.error('Error loading grows:', error);
        growListElement.innerHTML = '<p style="text-align: center; color: #ef4444;">Error al cargar cultivos</p>';
    }
}

function wireGrowListActions() {
    const list = document.getElementById('growList');
    if (!list) return;
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

export { fetchStageTimeline, showNewGrowModal, showStageModal, showGrowModal, fetchActiveGrow, createGrow, updateStage, activateGrow, endGrow, wireGrowListActions };
