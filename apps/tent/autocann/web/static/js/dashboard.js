import { apiGet, apiPost } from './api.js';
import { renderStageMarkers, updateHistoricalCharts } from './charts.js';
import { alertDialog, closeModal, showModal, toast } from './modals.js';
import { MODE_ACTIVE, MODE_MONITOR, MODE_PAUSED, state } from './state.js';
import { outputRowState, outputShortName } from './status.js';
import {
    escapeHtml, fmtComma, fmtNum, formatDuration, icon, relaySourceLabel, withGrow,
} from './util.js';

/** Live readings, period summary, the pause state and the relay rows. */

// ===============================
// Ahora: the two readings the panel is read for
// ===============================
// The readouts are where temperature and humidity are shown on the panel. The
// status line and the VPD chart need the same reading, so they are handed it
// as an event rather than fetching it a second time: one request, one answer.

/** Band edges are whole numbers in practice; a fraction keeps its tenth. */
function bandEdge(value) {
    return Number.isInteger(value) ? String(value) : fmtComma(value, 1);
}

function setReadoutValue(id, value, unit, { empty = false, warn = false } = {}) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = `${escapeHtml(value)}<span class="ac-readout__unit"> ${escapeHtml(unit)}</span>`;
    el.classList.toggle('is-empty', empty);
    el.classList.toggle('is-warn', warn);
}

function setReadoutSub(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function renderReadouts(data) {
    const band = Array.isArray(data.humidity_band) && data.humidity_band.length === 2
        ? data.humidity_band : null;
    const [low, high] = band || [];
    const humidityOk = Number.isFinite(data.humidity);
    // Truthiness, not `!== null`: an error body arrives with every field
    // undefined, and an undefined band must read as "no band".
    const below = humidityOk && !!band && data.humidity < low;
    const above = humidityOk && !!band && data.humidity > high;

    setReadoutValue('panel-temp', fmtComma(data.temperature, 1), '°C',
        { empty: !Number.isFinite(data.temperature) });
    setReadoutSub('panel-temp-sub', Number.isFinite(data.outside_temperature)
        ? `afuera ${fmtComma(data.outside_temperature, 1)} °C` : 'afuera --');

    // Warn only when the humidity is outside its band: inside it, the colour
    // would be claiming a problem the readout does not have.
    setReadoutValue('panel-humidity', fmtComma(data.humidity, 1), '%',
        { empty: !humidityOk, warn: below || above });
    let sub = band ? `objetivo ${bandEdge(low)} – ${bandEdge(high)}` : 'objetivo --';
    if (below) sub += ` · ${Math.round(low - data.humidity)} por debajo`;
    if (above) sub += ` · ${Math.round(data.humidity - high)} por encima`;
    setReadoutSub('panel-humidity-sub', sub);
}

function updateCurrentData(data) {
    // No reading is shown as no reading: a dash, not a number that passes for
    // one. A 404 lands here too, as `{error}`.
    renderReadouts(data);
    document.dispatchEvent(new CustomEvent('current:updated', { detail: data }));

    // The time, and nothing else. What it is for is answering "is this live?"
    // at a glance; the full stamp is in the tooltip.
    const now = new Date();
    const stamp = document.getElementById('last-update');
    stamp.textContent = now.toLocaleTimeString('es-AR', {
        hour: '2-digit', minute: '2-digit', hour12: false,
    });
    stamp.title = `Última actualización: ${now.toLocaleString('es-AR', { hour12: false })}`;
}

// El estado de los sensores -y el de todo lo demas- vive en health.js, que
// lo saca de /api/health: un solo veredicto para el badge del hero, las
// tarjetas y el cartel de arriba, que antes podian decir cosas distintas.

function updatePeriodSummary(data) {
    if (!data || data.sample_count === 0) return;

    // `x !== null ? x : '--'` is true for undefined too, so a missing key
    // rendered the literal "undefined". fmtNum covers both cases.
    const set = (id, value, decimals, unit) => {
        document.getElementById(id).textContent = fmtNum(value, decimals) + unit;
    };
    const setHero = (id, value, decimals, unit) => {
        document.getElementById(id).innerHTML =
            fmtNum(value, decimals) + `<span class="hero-unit">${unit}</span>`;
    };

    setHero('summary-temp-avg', data.temperature?.avg, 1, '°C');
    set('summary-temp-min', data.temperature?.min, 1, '°C');
    set('summary-temp-max', data.temperature?.max, 1, '°C');

    setHero('summary-humidity-avg', data.humidity?.avg, 1, '%');
    set('summary-humidity-min', data.humidity?.min, 1, '%');
    set('summary-humidity-max', data.humidity?.max, 1, '%');

    setHero('summary-vpd-avg', data.vpd?.avg, 2, 'kPa');
    set('summary-vpd-min', data.vpd?.min, 2, ' kPa');
    set('summary-vpd-max', data.vpd?.max, 2, ' kPa');

    set('summary-outside-temp', data.outside_temperature?.avg, 1, '°C');
    set('summary-outside-humidity', data.outside_humidity?.avg, 1, '%');

    document.getElementById('summary-samples').textContent =
        Number.isFinite(data.sample_count)
            ? data.sample_count.toLocaleString('es-AR') : '--';
}

function updateSummaryPeriodLabel() {
    const label = document.getElementById('summary-period-label');
    if (!label) return;  // Element doesn't exist, skip update
    if (state.hours !== null) {
        if (state.hours < 1) {
            label.textContent = `Últimos ${Math.round(state.hours * 60)} minutos`;
        } else {
            label.textContent = `Últimas ${state.hours}h`;
        }
    } else if (state.period === 1) {
        // The element this writes into did not exist in any template, so the
        // function returned early and "Últimos 1 día" was never on screen.
        label.textContent = 'Último día';
    } else {
        label.textContent = `Últimos ${state.period} días`;
    }
}

function fetchPeriodSummary() {
    let url;
    if (state.hours !== null) {
        url = `/api/period-summary?hours=${state.hours}`;
    } else {
        url = `/api/period-summary?days=${state.period}`;
    }

    apiGet(withGrow(url, state.growId), { key: 'summary' })
        .then(data => { if (data) updatePeriodSummary(data); })
        .catch(error => console.error('Error fetching period summary:', error));
}

function updateDatabaseStats(stats) {
    if (stats.database_size_mb !== undefined) {
        document.getElementById('stat-db-size').textContent = `${stats.database_size_mb} MB`;
    }
    if (stats.grow_count) {
        document.getElementById('stat-grows').textContent = stats.grow_count;
    }
}

// ===============================
// Pause
// ===============================
// While it is on the control loop stands down: the relays follow what is asked
// here and nothing is written to the history. It ends when somebody ends it -
// it used to expire after eight hours, and what made that unsafe is that the
// relays then closed again at a moment nobody chose. What makes this safe to
// offer instead is that the state is impossible to miss: the header says it,
// in amber, on every view, for as long as it lasts.

/** Last known pause state, from /api/output-status so it cannot disagree. */
let testMode = { active: false };
/** Whether the backend is answering, from api.js. */
let offline = false;

/**
 * How long it has been, in one token.
 *
 * Not formatDuration: that one spells out "44 h 32 min", and the minutes are
 * noise on a state that has lasted two days - this is read to know roughly how
 * long, never to the minute. It also describes time *left*, so it reads zero
 * and below as "no duration" and renders "--", when zero seconds of elapsed
 * time is a real answer: it means you just pressed the button.
 */
function pausedFor(seconds) {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return 'hace un rato';
    if (seconds < 60) return 'recién';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours} h`;
    return `${Math.floor(hours / 24)} d`;
}

/**
 * The header chip, which is the whole announcement.
 *
 * There was a full-width amber banner under the header as well, repeating the
 * chip in three more sentences on every view. Two elements saying one thing is
 * how you end up reading neither, so the banner is gone and what it had that a
 * chip did not - how long the pause has lasted, and that nothing but a person
 * ends it - is in the chip and its tooltip. The chip is still impossible to
 * miss while it lasts: it goes amber, keeps the dot pulsing, and is on every
 * view because the header is.
 *
 * One renderer for two inputs, because one element cannot have two owners: a
 * backend that stopped answering and a controller that was paused are
 * different things, and the chip has to say which. Not answering wins - while
 * it lasts, what the last payload said about the pause is a guess.
 */
function renderSystemState() {
    const chip = document.getElementById('system-state');
    const dot = document.getElementById('system-state-dot');
    const text = document.getElementById('system-state-text');
    const action = document.getElementById('system-state-action');
    const elapsed = document.getElementById('pause-elapsed');
    if (!chip) return;

    // A tent that is not being controlled is still not being controlled when
    // the mode is one this build does not recognise, so anything unknown reads
    // as stopped rather than as running.
    const stopped = !!testMode.active;
    const monitoring = stopped && testMode.mode === MODE_MONITOR;

    chip.classList.toggle('is-paused', stopped && !monitoring && !offline);
    chip.classList.toggle('is-monitor', monitoring && !offline);
    chip.classList.toggle('is-offline', offline);
    dot.classList.toggle('offline', offline);

    // Empty rather than "--" when there is nothing to time: the span collapses
    // and the chip goes back to being two words wide.
    if (elapsed) {
        elapsed.textContent = (stopped && !offline) ? pausedFor(testMode.elapsed_seconds) : '';
    }

    if (offline) {
        text.textContent = 'Sin conexión';
        // Nothing to toggle: the request would not arrive, and a button that
        // looks live while the backend is gone is how you end up pressing it
        // four times and not knowing what took.
        action.textContent = '';
        chip.disabled = true;
        chip.title = 'No se puede contactar al backend';
        return;
    }

    chip.disabled = false;
    // The verb is what pressing it does, which is why both stopped states share
    // one: from either, the chip hands the tent back to the controller.
    text.textContent = monitoring ? 'Monitoreo'
        : stopped ? 'Sistema en pausa' : 'Sistema activo';
    action.textContent = stopped ? 'Controlar' : 'Detener';
    chip.title = monitoring
        ? 'El lazo no está controlando la carpa y nada se enciende, pero el '
          + 'histórico se sigue guardando. No se reanuda solo — sigue así hasta '
          + 'que lo saques, incluso si se reinicia la Raspberry.'
        : stopped
            ? 'El lazo no está controlando la carpa: los relés quedan donde los pongas '
              + 'y no se guarda nada en el histórico. No se reanuda solo — sigue así '
              + 'hasta que lo saques, incluso si se reinicia la Raspberry.'
            : 'Dejar de controlar: monitoreo, o pausa para trabajar en la carpa';
}

document.addEventListener('connection:changed', event => {
    offline = !!event.detail?.offline;
    renderSystemState();
});

// Remembered per relay: when this page first saw it on. The backend does not
// say when a relay switched, so after a reload the minutes count from the
// reload rather than from the switch - the row says so by showing less, not
// by being wrong about it.
const onSince = {};

/** The outputs with `since` filled in for any relay that is on. */
function outputsWithSince(outputs) {
    const nowSeconds = Date.now() / 1000;
    return outputs.map(output => {
        if (output.state !== true) {
            delete onSince[output.name];
            return output;
        }
        if (!(output.name in onSince)) onSince[output.name] = nowSeconds;
        return { ...output, since: onSince[output.name] };
    });
}

function outputRowsHtml(outputs) {
    return outputs.map(output => {
        const row = outputRowState(output);
        return `
            <div class="ac-output${row.state === 'on' ? ' is-on' : ''}">
                <span class="ac-output__name">${escapeHtml(outputShortName(output))}</span>
                <span class="ac-output__state">${escapeHtml(row.text)}</span>
            </div>`;
    }).join('');
}

/**
 * The manual relay controls, under the rows, and only while the loop is not in
 * charge: the buttons are how a person drives a relay, and they mean nothing
 * while the loop is deciding.
 */
function manualControlsHtml(outputs, mode) {
    return outputs.filter(output => output.name).map(output => {
        const requested = mode.requested_outputs?.[output.name];
        // A relay that does not move must say why, or the button looks broken.
        // The minimum on/off times hold even for a person.
        const held = (typeof output.held_seconds === 'number' && output.held_seconds > 0)
            ? `<span class="panel-manual__held">${icon('hourglass')}esperando ${formatDuration(output.held_seconds)} (mínimo de seguridad)</span>`
            : '';
        const name = escapeHtml(output.name);
        return `
            <div class="panel-manual__row">
                <span class="ac-output__name">${escapeHtml(outputShortName(output))}</span>
                <span class="panel-manual__buttons">
                    <button type="button" class="ac-btn ac-btn--secondary" aria-pressed="${requested === true}"
                            data-action="test-output" data-name="${name}" data-state="true">Prender</button>
                    <button type="button" class="ac-btn ac-btn--secondary" aria-pressed="${requested === false}"
                            data-action="test-output" data-name="${name}" data-state="false">Apagar</button>
                </span>
                ${held}
            </div>`;
    }).join('');
}

function updateOutputsStatus(payload) {
    const outputs = outputsWithSince(payload?.outputs || []);

    testMode = payload?.test_mode || { active: false };
    renderSystemState();

    // Qué placa conmuta, dicho una vez: las salidas cuelgan de la misma.
    const source = document.getElementById('outputs-source');
    if (source) {
        const label = relaySourceLabel(outputs[0]?.pin);
        source.textContent = label.text;
        source.title = label.title;
    }

    const list = document.getElementById('outputs-list');
    if (list) {
        list.innerHTML = outputs.length
            ? outputRowsHtml(outputs)
            : '<div class="ac-output"><span class="ac-output__name">No configuradas</span>'
                + '<span class="ac-output__state">--</span></div>';
    }

    const manual = document.getElementById('outputs-manual');
    if (manual) {
        const show = testMode.active && outputs.length > 0;
        manual.innerHTML = show ? manualControlsHtml(outputs, testMode) : '';
        manual.hidden = !show;
    }

    document.dispatchEvent(new CustomEvent('outputs:updated', {
        detail: { outputs, test_mode: testMode },
    }));
}

/**
 * How each stopped state announces itself once it has been entered.
 *
 * Monitoring is not an error, so it does not get the red toast a pause does:
 * one is somebody stopping the tent to work on it, the other is somebody
 * deliberately watching a room that has nothing in it yet.
 */
const STOP_TOASTS = {
    [MODE_MONITOR]: ['Monitoreo: nada se enciende, el histórico sigue', { kind: 'ok' }],
    [MODE_PAUSED]: ['Sistema en pausa', { kind: 'error' }],
};

/**
 * Ask which way to stop.
 *
 * Not a confirmDialog: that one has a button and a cancel, and the question
 * here is *which* stop. The modal puts the two side by side with what each
 * does to the relays and to the history, because that difference is the whole
 * reason there are two.
 */
function askHowToStop() {
    showModal('stopModeModal');
}

async function setMode(mode) {
    const result = await apiPost('/api/test-mode', { mode });
    if (!result.ok) {
        await alertDialog({ title: 'No se pudo cambiar el estado', message: result.error });
        return;
    }
    const [message, options] = STOP_TOASTS[mode] || ['Control automático de vuelta', {}];
    toast(message, options);
    fetchOutputsStatus();
}

async function enterMonitorMode() {
    closeModal('stopModeModal');
    await setMode(MODE_MONITOR);
}

async function enterTestMode() {
    closeModal('stopModeModal');
    await setMode(MODE_PAUSED);
}

async function exitTestMode() {
    await setMode(MODE_ACTIVE);
}

/**
 * The header chip.
 *
 * Asymmetric on purpose. Stopping is a fork - monitoring or pause - so it opens
 * the modal that asks which. Going back to control is one thing, so it is one
 * click: the dialog on the way in is where the consequences were explained,
 * and asking again on the way out would be asking somebody to confirm that
 * they want their tent looked after.
 */
function toggleTestMode() {
    if (testMode.active) exitTestMode();
    else askHowToStop();
}

async function setTestOutput(trigger) {
    const name = trigger?.dataset?.name;
    if (!name) return;

    const result = await apiPost('/api/test-mode/outputs', {
        name,
        state: trigger.dataset.state === 'true',
    });
    if (!result.ok) {
        await alertDialog({ title: 'No se pudo mover el relé', message: result.error });
    }
    // Refresh either way: on failure the buttons must go back to showing what
    // is actually requested, not what was clicked.
    fetchOutputsStatus();
}

// API Fetch functions

function fetchCurrentData() {
    apiGet('/api/current-data').then(data => { if (data) updateCurrentData(data); });
}

function fetchOutputsStatus() {
    apiGet('/api/output-status').then(data => { if (data) updateOutputsStatus(data); });
}

function fetchDatabaseStats() {
    apiGet('/api/database-stats').then(data => { if (data) updateDatabaseStats(data); });
}

function fetchHistoricalData(days, interval, hours = null) {
    let url;

    if (hours !== null) {
        const now = Math.floor(Date.now() / 1000);
        const start = now - (hours * 3600);
        url = `/api/sensor-history?start=${start}&end=${now}&limit=1000`;
    } else {
        url = `/api/history/aggregated?days=${days}&interval=${interval}`;
    }

    // Keyed by endpoint, not by URL, so switching time range cancels
    // nothing but still never stacks two history requests.
    apiGet(withGrow(url, state.growId), { key: 'history' }).then(data => {
        if (!data) return;
        updateHistoricalCharts(hours !== null
            ? { data: data.data || [], count: data.count || 0, aggregated: false }
            : data);
        renderGrowScopeNote(data);
        // The x-axis just changed, so the markers need repositioning.
        // Only for the active grow: the markers come from its stage timeline,
        // and drawing them over another grow's curve would date its stages
        // from somebody else's calendar.
        if (state.timeline && state.growId === null) renderStageMarkers(state.timeline);
    });
}

/**
 * Say which grow the charts are about, when it is not the obvious one.
 *
 * The scope is resolved on the server — leaving the parameter out means "the
 * active grow" — so a chart that does not ask has no way to label itself, and
 * one whose meaning changes the moment somebody activates another grow,
 * silently, is worse than one with no label.
 */
function renderGrowScopeNote(payload) {
    const note = document.getElementById('grow-scope-note');
    if (!note) return;

    const scope = payload?.grow_scope;
    if (scope === 'all') {
        note.textContent = 'Todos los cultivos. Las curvas se cortan en cada cambio de cultivo.';
    } else if (scope === 'none') {
        note.textContent = 'No hay ningún cultivo activo: nada que mostrar hasta que arranques uno.';
    } else {
        note.textContent = '';
    }
    note.hidden = !note.textContent;
}

export {
    updateSummaryPeriodLabel, fetchPeriodSummary, fetchCurrentData,
    fetchOutputsStatus, fetchDatabaseStats, fetchHistoricalData, renderGrowScopeNote,
    enterMonitorMode, enterTestMode, exitTestMode, toggleTestMode, setTestOutput,
    renderSystemState,
};
