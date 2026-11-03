/**
 * Control remoto: the humidifier's IR remote, as buttons on the panel.
 *
 * A press is parked on the backend and the relay node sends it on its next
 * poll (autocann/hardware/ir_remote.py), so a 200 from the press only means
 * "queued". The line under the buttons waits for the node's acknowledgement
 * before saying it went out, and says so when it did not.
 */

import { apiGet, apiPost } from './api.js';
import { errorMessage } from './errors.js';
import { escapeHtml } from './util.js';

/** The output that powers the humidifier; without it a press reaches nothing. */
const TARGET_OUTPUT = 'humidity_up';

const CHECK_EVERY_MS = 1000;

const remote = {
    buttons: [],
    ttlSeconds: 10,
    /** null until the first outputs poll: unknown is not "off". */
    powered: null,
    /** The press waiting for the node: { id, label, until }. */
    inFlight: null,
};

function el(id) {
    return document.getElementById(id);
}

/**
 * Where a press stands, from a /api/ir-remote answer.
 *
 * Pure, and exported for the tests: `sent` once the node has acknowledged it,
 * `waiting` while it is still the pending one, and `lost` once it is neither —
 * it expired, which is what happens when the node did not poll in time.
 */
function pressOutcome(status, id) {
    if (status?.last?.id === id) return 'sent';
    if (status?.pending?.id === id) return 'waiting';
    return 'lost';
}

function setStatus(text) {
    const line = el('ir-remote-status');
    if (line) line.textContent = text;
}

function render() {
    const grid = el('ir-remote-buttons');
    if (!grid) return;

    const disabled = remote.powered === false || remote.inFlight !== null;
    grid.innerHTML = remote.buttons.map(button => `
        <button type="button" class="ac-btn ac-btn--secondary" data-ir-button="${escapeHtml(button.key)}"
                title="${escapeHtml(button.hint)}" ${disabled ? 'disabled' : ''}>${escapeHtml(button.label)}</button>`)
        .join('');

    if (!remote.inFlight && remote.powered === false) {
        setStatus('Sin energía: el relé del humidificador está apagado.');
    }
}

function finish(text) {
    remote.inFlight = null;
    setStatus(text);
    render();
}

async function checkPress() {
    const press = remote.inFlight;
    if (!press) return;

    const status = await apiGet('/api/ir-remote', { key: 'ir-remote-check' });
    if (remote.inFlight !== press) return;

    const outcome = status ? pressOutcome(status, press.id) : 'waiting';
    if (outcome === 'sent') return finish(`Enviado: ${press.label}.`);
    if (outcome === 'lost' || Date.now() > press.until) {
        return finish(`${press.label} no llegó: el nodo de relés no lo levantó a tiempo.`);
    }
    setTimeout(checkPress, CHECK_EVERY_MS);
}

async function press(key) {
    const button = remote.buttons.find(b => b.key === key);
    if (!button || remote.inFlight) return;

    const result = await apiPost('/api/ir-remote/press', { button: key });
    if (!result.ok) {
        // No code means it never got an answer: "no se pudo guardar" would lie.
        setStatus(result.code ? errorMessage(result) : 'No se pudo mandar el botón: el panel no contesta.');
        return;
    }

    remote.inFlight = {
        id: result.data.command.id,
        label: button.label,
        // A little past the backend's own expiry, so the answer comes from it.
        until: Date.now() + ((result.data.ttl_seconds || remote.ttlSeconds) + 3) * 1000,
    };
    setStatus(`Mandando ${button.label}...`);
    render();
    setTimeout(checkPress, CHECK_EVERY_MS);
}

async function wireRemote() {
    const grid = el('ir-remote-buttons');
    if (!grid) return;

    grid.addEventListener('click', event => {
        const button = event.target.closest('[data-ir-button]');
        if (button && !button.disabled) press(button.dataset.irButton);
    });

    // The relay state is already polled for the Salidas rows: reuse it rather
    // than asking again every few seconds.
    document.addEventListener('outputs:updated', event => {
        const output = (event.detail?.outputs || []).find(o => o.name === TARGET_OUTPUT);
        const powered = output ? Boolean(output.state) : null;
        if (powered === remote.powered) return;
        remote.powered = powered;
        if (powered && !remote.inFlight) setStatus('');
        render();
    });

    const status = await apiGet('/api/ir-remote');
    if (!status) return;
    remote.buttons = status.buttons || [];
    remote.ttlSeconds = status.ttl_seconds || remote.ttlSeconds;
    if (remote.powered === null) remote.powered = status.powered;
    render();
}

export { pressOutcome, wireRemote };
