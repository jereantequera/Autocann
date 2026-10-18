/**
 * The tent's name, editable in Sistema.
 *
 * The rig has one tent, id 1. The name is written by PUT /api/tents/1 and then
 * shown by the sidebar at once (setTentName), without waiting for a reload.
 */

import { errorMessage } from './errors.js';
import { toast } from './modals.js';
import { setTentName } from './sidebar.js';

const TENT_ID = 1;
export const TENT_NAME_MAX = 60;

/**
 * The name as the server will keep it, or why it will not: trimmed, 1 to 60
 * characters. Counted in code points, the way the server counts them, so an
 * emoji is one character here too.
 */
export function checkTentName(raw) {
    const name = String(raw ?? '').trim();
    if (!name) return { ok: false, error: 'Ponele un nombre a la carpa.' };
    if ([...name].length > TENT_NAME_MAX) {
        return { ok: false, error: `El nombre puede tener hasta ${TENT_NAME_MAX} caracteres.` };
    }
    return { ok: true, name };
}

function showError(message) {
    const error = document.getElementById('tent-name-error');
    const input = document.getElementById('tent-name-input');
    if (error) {
        error.textContent = message;
        error.hidden = !message;
    }
    if (input) input.setAttribute('aria-invalid', message ? 'true' : 'false');
}

async function saveTentName(event) {
    event.preventDefault();
    const input = document.getElementById('tent-name-input');
    const checked = checkTentName(input.value);
    if (!checked.ok) {
        showError(checked.error);
        input.focus();
        return;
    }

    try {
        const response = await fetch(`/api/tents/${TENT_ID}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: checked.name }),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) {
            // The server's own words are for logs; the person gets one line.
            console.warn('Tent name refused:', body);
            showError(errorMessage(body));
            input.focus();
            return;
        }
        showError('');
        input.value = body.name;
        setTentName(body.name);
        toast('Nombre de la carpa guardado');
    } catch (error) {
        console.error('Error saving the tent name:', error);
        showError('No se pudo contactar al backend.');
    }
}

/** Wires the Sistema form. A page without it (none today) is left alone. */
export function wireTentName() {
    const form = document.getElementById('tent-name-form');
    if (form) form.addEventListener('submit', saveTentName);
}
