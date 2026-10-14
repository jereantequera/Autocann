import { apiGet, apiPost } from './api.js';
import { renderStageMarkers, updateHistoricalCharts } from './charts.js';
import { alertDialog, confirmDialog, toast } from './modals.js';
import { config, state } from './state.js';
import { escapeHtml, fmtNum, formatDuration, humidityDiffLabel, withGrow } from './util.js';

/** Live readings, period summary, sensor badges and relay cards. */

function updateVpdGauge(vpd) {
    const marker = document.getElementById('vpd-marker');
    if (!marker) return;

    const vpdAbsMax = 2.0;
    const range = config.vpdRanges[state.stage] || config.vpdRanges.late_veg;
    const vpdMin = range.min;
    const vpdMax = range.max;

    // Update zone widths dynamically from current stage range
    const lowPct  = (vpdMin / vpdAbsMax) * 100;
    const optPct  = ((vpdMax - vpdMin) / vpdAbsMax) * 100;
    const highPct = ((vpdAbsMax - vpdMax) / vpdAbsMax) * 100;
    document.querySelector('.vpd-zone.low').style.width     = lowPct  + '%';
    document.querySelector('.vpd-zone.optimal').style.width = optPct  + '%';
    document.querySelector('.vpd-zone.high').style.width    = highPct + '%';

    // Update gauge labels
    const labels = document.querySelectorAll('.vpd-gauge-labels span');
    if (labels.length === 4) {
        labels[0].textContent = '0.0';
        labels[1].textContent = vpdMin.toFixed(1);
        labels[2].textContent = vpdMax.toFixed(1);
        labels[3].textContent = vpdAbsMax.toFixed(1);
    }

    // Position marker; skip if no value
    if (vpd === null || vpd === undefined) return;
    const position = Math.min(Math.max((vpd / vpdAbsMax) * 100, 0), 100);
    marker.style.left = `${position}%`;
}

// Cada pieza de la barra depende de una lectura distinta y cualquiera puede
// faltar, así que cada una se dibuja sólo si su propio número existe.

function updateHumidityBar(humidity, targetHumidity) {
    const fillEl = document.getElementById('humidity-bar-fill');
    const targetEl = document.getElementById('humidity-bar-target');
    const diffEl = document.getElementById('humidity-diff');

    if (fillEl && Number.isFinite(humidity)) {
        fillEl.style.width = `${Math.min(Math.max(humidity, 0), 100)}%`;
    }

    if (targetEl && Number.isFinite(targetHumidity)) {
        targetEl.style.left = `${Math.min(Math.max(targetHumidity, 0), 100)}%`;
    }

    // Sin lectura no hay diferencia: el cartel vuelve a quedar vacío, como nace
    // en la plantilla, en lugar de anunciar una resta contra undefined.
    if (diffEl) {
        const label = humidityDiffLabel(humidity, targetHumidity);
        diffEl.className = label ? `humidity-diff ${label.state}` : 'humidity-diff';
        diffEl.textContent = label ? label.text : '';
    }
}

function updateCurrentData(data) {
    document.getElementById('current-temp').innerHTML =
        fmtNum(data.temperature, 1) + '<span class="hero-unit">°C</span>';
    document.getElementById('current-humidity').innerHTML =
        fmtNum(data.humidity, 1) + '<span class="hero-unit">%</span>';
    document.getElementById('current-target-humidity').innerHTML =
        fmtNum(data.target_humidity, 1) + '<span class="hero-unit">%</span>';
    document.getElementById('current-vpd').innerHTML =
        fmtNum(data.leaf_vpd, 2) + '<span class="hero-unit">kPa</span>';
    document.getElementById('current-leaf-temp').textContent =
        fmtNum(data.leaf_temperature, 1);
    document.getElementById('current-outdoor-temp').innerHTML =
        fmtNum(data.outside_temperature, 1) + '<span class="metric-unit">°C</span>';
    document.getElementById('current-outdoor-humidity').innerHTML =
        fmtNum(data.outside_humidity, 1) + '<span class="metric-unit">%</span>';
    
    // Update VPD gauge
    updateVpdGauge(data.leaf_vpd);
    
    // Update humidity bar
    updateHumidityBar(data.humidity, data.target_humidity);
    
    // Update VPD status
    const vpdCard = document.getElementById('vpd-card');
    const vpdStatus = document.getElementById('vpd-status');
    const vpdHint = document.getElementById('vpd-target-hint');
    
    const range = config.vpdRanges[state.stage];
    if (data.vpd_in_range === true) {
        vpdCard.classList.remove('vpd-adjusting');
        vpdCard.classList.add('vpd-optimal');
        vpdStatus.className = 'vpd-badge in-range';
        vpdStatus.innerHTML = '✓ En rango óptimo';
        vpdHint.textContent = `(${range.min}-${range.max} kPa)`;
    } else if (data.vpd_in_range === false) {
        vpdCard.classList.remove('vpd-optimal');
        vpdCard.classList.add('vpd-adjusting');
        vpdStatus.className = 'vpd-badge adjusting';
        vpdStatus.innerHTML = '⚡ Ajustando...';
        vpdHint.textContent = `Objetivo: ${range.min}-${range.max} kPa`;
    } else {
        vpdCard.classList.remove('vpd-optimal', 'vpd-adjusting');
        vpdStatus.className = 'vpd-badge';
        vpdStatus.textContent = '';
        vpdHint.textContent = '';
    }
    
    document.getElementById('last-update').textContent = 
        `Actualizado: ${new Date().toLocaleString('es-AR', { hour12: false })}`;
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
 * How long the pause has lasted, in words.
 *
 * Not formatDuration: that one describes time *left*, so it reads zero and
 * below as "no duration" and renders "--". Zero seconds of elapsed time is a
 * real answer - it means you just pressed the button - and "en pausa desde
 * hace --" reads like something failed to load.
 */
function pausedFor(seconds) {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return 'un momento';
    if (seconds < 60) return 'menos de un minuto';
    return formatDuration(seconds);
}

/**
 * The header chip, and the bar under it.
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
    const bar = document.getElementById('pause-bar');
    const elapsed = document.getElementById('pause-elapsed');
    if (!chip) return;

    const paused = !!testMode.active;

    if (bar) bar.classList.toggle('show', paused && !offline);
    if (elapsed) elapsed.textContent = pausedFor(testMode.elapsed_seconds);

    chip.classList.toggle('is-paused', paused && !offline);
    chip.classList.toggle('is-offline', offline);
    dot.classList.toggle('offline', offline);

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
    text.textContent = paused ? 'Sistema en pausa' : 'Sistema activo';
    action.textContent = paused ? 'Reanudar' : 'Pausar';
    chip.title = paused
        ? 'El lazo no está controlando la carpa'
        : 'Detener el control automático para trabajar en la carpa';
}

document.addEventListener('connection:changed', event => {
    offline = !!event.detail?.offline;
    renderSystemState();
});

function outputCardHtml(output, mode) {
    let stateText = '--';
    let stateClass = 'unknown';
    if (output.state === true) {
        stateText = 'ENCENDIDA';
        stateClass = 'on';
    } else if (output.state === false) {
        stateText = 'APAGADA';
        stateClass = 'off';
    }

    const label = escapeHtml(output.label || output.name || 'Salida');
    const name = escapeHtml(output.name || '');
    const pin = (output.pin_bcm !== undefined && output.pin_bcm !== null)
        ? `BCM ${Number(output.pin_bcm)}` : '';

    let controls = '';
    if (mode.active && output.name) {
        const requested = mode.requested_outputs?.[output.name];
        // A relay that does not move must say why, or the button looks broken.
        // The minimum on/off times hold even for a person: the equipment does
        // not care who asked.
        const held = (typeof output.held_seconds === 'number' && output.held_seconds > 0)
            ? `<div class="output-held">⏳ esperando ${formatDuration(output.held_seconds)} (mínimo de seguridad)</div>`
            : '';
        controls = `
            <div class="output-controls">
                <button class="btn-output on ${requested === true ? 'active' : ''}"
                        data-action="test-output" data-name="${name}" data-state="true">Prender</button>
                <button class="btn-output off ${requested === false ? 'active' : ''}"
                        data-action="test-output" data-name="${name}" data-state="false">Apagar</button>
            </div>
            ${held}
        `;
    }

    return `
        <div class="output-card ${mode.active ? 'is-manual' : ''}">
            <div class="output-header">
                <div class="output-name">${label}</div>
                <div class="output-pin">${pin}</div>
            </div>
            <div class="output-state ${stateClass}">${stateText}</div>
            ${controls}
        </div>
    `;
}

function updateOutputsStatus(payload) {
    const grid = document.getElementById('outputs-grid');
    const outputs = payload?.outputs || [];

    testMode = payload?.test_mode || { active: false };
    renderSystemState();

    if (!grid) return;

    if (!outputs.length) {
        grid.innerHTML = `
            <div class="output-card">
                <div class="output-name">No configuradas</div>
                <div class="output-state unknown">--</div>
            </div>
        `;
        return;
    }

    grid.innerHTML = outputs.map(o => outputCardHtml(o, testMode)).join('');
}

async function enterTestMode() {
    // Said plainly because it is now true: nothing ends this but a person.
    const confirmed = await confirmDialog({
        title: 'Pausar el control de la carpa',
        message: 'El lazo deja de controlar: los relés van a quedar donde vos los '
            + 'pongas y no se va a guardar nada en el histórico.\n\n'
            + 'No se reanuda solo. Sigue en pausa hasta que lo saques, aunque se '
            + 'reinicie la Raspberry.',
        confirmLabel: 'Pausar el sistema',
    });
    if (!confirmed) return;

    const result = await apiPost('/api/test-mode', { enabled: true });
    if (!result.ok) {
        await alertDialog({ title: 'No se pudo pausar', message: result.error });
        return;
    }
    toast('Sistema en pausa', { kind: 'error' });
    fetchOutputsStatus();
}

async function exitTestMode() {
    const result = await apiPost('/api/test-mode', { enabled: false });
    if (!result.ok) {
        await alertDialog({ title: 'No se pudo reanudar', message: result.error });
        return;
    }
    toast('Control automático de vuelta');
    fetchOutputsStatus();
}

/** The header chip: one control, so which way it goes depends on the state. */
function toggleTestMode() {
    if (testMode.active) exitTestMode();
    else enterTestMode();
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
    enterTestMode, exitTestMode, toggleTestMode, setTestOutput, renderSystemState,
};
