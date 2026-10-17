/**
 * The Fotoperíodo control in Cultivo: when the active grow's lights come on and
 * go off, and the button that saves it.
 *
 * The helpers at the top are pure. Nothing touches the document until the
 * control is wired, so the node tests can import them.
 */

import { apiGet } from './api.js';

/** "06:00" for hour 6. */
function hourLabel(hour) {
    return `${String(hour).padStart(2, '0')}:00`;
}

/** Hours the lights are on in one day for a window, midnight-crossing included. */
function lightsHours(on, off) {
    return (off - on + 24) % 24;
}

/** "Luces de 06:00 a 00:00: 18 h." */
function photoperiodText(on, off) {
    return `Luces de ${hourLabel(on)} a ${hourLabel(off)}: ${lightsHours(on, off)} h.`;
}

/** The reason a pair of hours cannot be saved, or null when it can. */
function photoperiodProblem(on, off) {
    if (on === off) return 'Encender y apagar no pueden ser la misma hora.';
    return null;
}

let growId = null;

function setStatus(text, isError = false) {
    const status = document.getElementById('photoperiod-status');
    if (!status) return;
    status.textContent = text;
    status.classList.toggle('is-warn', isError);
}

/** Both selects list the 24 hours, with the saved one selected. */
function fillSelect(select, selectedHour) {
    select.replaceChildren(...Array.from({ length: 24 }, (_, hour) =>
        new Option(hourLabel(hour), String(hour), false, hour === selectedHour)));
}

function setControlsEnabled(enabled) {
    for (const id of ['photoperiod-on', 'photoperiod-off', 'photoperiod-save']) {
        const el = document.getElementById(id);
        if (el) el.disabled = !enabled;
    }
}

/** Reads the active grow's photoperiod into the selects. */
async function fetchPhotoperiod() {
    // Its own key: apiGet drops a second request for the same key while the
    // first is in flight, and the boot's active-grow fetch is for this URL too.
    const grow = await apiGet('/api/grows/active', { key: 'photoperiod-active-grow' });
    if (!grow || grow.error || !Number.isInteger(grow.id)) {
        growId = null;
        setControlsEnabled(false);
        setStatus('No hay un cultivo activo para configurar.');
        return;
    }
    growId = grow.id;

    const found = await apiGet(`/api/grows/${growId}/photoperiod`);
    if (!found || found.error) {
        setControlsEnabled(false);
        setStatus('No se pudo leer el fotoperíodo. Probá de nuevo.', true);
        return;
    }
    fillSelect(document.getElementById('photoperiod-on'), found.on);
    fillSelect(document.getElementById('photoperiod-off'), found.off);
    setControlsEnabled(true);
    const suffix = found.configured ? '' : ' Todavía no lo configuraste: es el horario por defecto.';
    setStatus(photoperiodText(found.on, found.off) + suffix);
}

async function savePhotoperiod() {
    if (growId === null) return;
    const on = Number(document.getElementById('photoperiod-on').value);
    const off = Number(document.getElementById('photoperiod-off').value);
    const problem = photoperiodProblem(on, off);
    if (problem) {
        setStatus(problem, true);
        return;
    }

    setControlsEnabled(false);
    try {
        const response = await fetch(`/api/grows/${growId}/photoperiod`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ on, off }),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const saved = await response.json();
        setStatus(`Guardado. ${photoperiodText(saved.on, saved.off)}`);
    } catch (error) {
        console.error('Photoperiod save failed:', error);
        setStatus('No se pudo guardar. Probá de nuevo.', true);
    } finally {
        setControlsEnabled(true);
    }
}

/** Loads the control on arrival at Cultivo, and wires its button once. */
function wirePhotoperiod() {
    const save = document.getElementById('photoperiod-save');
    if (!save) return;
    save.addEventListener('click', savePhotoperiod);
    document.addEventListener('view:enter', event => {
        if (event.detail?.view === 'cultivo') fetchPhotoperiod();
    });
}

export { hourLabel, lightsHours, photoperiodProblem, photoperiodText, wirePhotoperiod };
