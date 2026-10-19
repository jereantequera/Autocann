/**
 * Feeding: today's doses, the irrigation log, and the editor for the chart.
 *
 * The chart lives in the database and the stage comes from the active grow, so
 * this module holds no schedule of its own — only what the server last sent.
 *
 * Entry point for the Riego view; the pieces live in feeding-*.js. Every name
 * exported here is exported as it was before the split.
 */

import { apiGet } from './api.js';
import { errorMessage } from './errors.js';
import { alertDialog, confirmDialog, toast } from './modals.js';
import { announce, el, feedState } from './feeding-state.js';
import { readForm, refreshTotals, renderForm, renderToday } from './feeding-form.js';
import { renderHistory } from './feeding-history.js';
import {
    addPlanProduct, fetchFeedPlan, markCurrentWeek, savePlan, togglePlanEditor, wirePlanEditor,
} from './feeding-plan.js';

/** Set once the grower edits the form, so a poll does not overwrite their input. */
let formTouched = false;

function fetchFeedingToday() {
    const litres = Number(el('feed-litres')?.value) || undefined;
    const url = litres ? `/api/feeding/today?litres=${litres}` : '/api/feeding/today';
    apiGet(url, { key: 'feeding-today' }).then(data => {
        if (!data || data.error) return;
        feedState.today = data;
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
        feedState.history = data.irrigations;
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
        if (!response.ok) throw new Error(errorMessage(result));

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
    if (feedState.today && Number.isFinite(feedState.today.litres)) el('feed-litres').value = feedState.today.litres;
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

export { addPlanProduct, fetchFeedPlan, fetchFeeding, fetchFeedingToday, fetchIrrigations, resetFormToPlan, saveIrrigation, savePlan, togglePlanEditor, wireFeeding };
