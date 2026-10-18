/**
 * Grow actions: the dialogs that create a grow, change its stage, activate or
 * finish one, and the handlers of the list's buttons.
 */

import { alertDialog, closeModal, confirmDialog, showModal, toast } from './modals.js';
import { fetchDatabaseStats, fetchHistoricalData } from './dashboard.js';
import { fetchHealth } from './health.js';
import { config, state } from './state.js';
import {
    buildGrowRequest, canSubmitGrow, clearGrowError, selectedProfile, showGrowError,
} from './growform.js';
import { fetchActiveGrow } from './grows-active.js';
import { loadGrowsList } from './grows-table.js';
import { fetchGrowFilter } from './grows-filter.js';

/**
 * Open the stage dialog, preselected: on the next stage when the primary button
 * asked for it, otherwise on the current one.
 */
export function showStageModal(trigger) {
    const select = document.getElementById('newStage');
    const wanted = trigger?.dataset?.stage || state.stage;
    if (select && wanted && [...select.options].some(option => option.value === wanted)) {
        select.value = wanted;
    }
    showModal('stageModal');
}

// Modal functions

export async function createGrow() {
    // The button is disabled until the stages load; this is the same rule for
    // any path that reaches the handler anyway.
    if (!canSubmitGrow()) return;
    clearGrowError();
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
            body: JSON.stringify(buildGrowRequest({ name, profileId: selectedProfile(), stage, notes }))
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
            // Inline, next to the button the person just pressed: a dialog on
            // top of the modal is easy to miss and loses the form behind it.
            showGrowError(result.error || 'El backend rechazó el pedido.');
        }
    } catch (error) {
        console.error('Error creating grow:', error);
        showGrowError('No se pudo contactar al backend.');
    }
}

export async function updateStage() {
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

export async function activateGrow(growId) {
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

export async function endGrow(growId) {
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

export function wireGrowListActions() {
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
