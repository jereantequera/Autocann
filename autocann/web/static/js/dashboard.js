import { apiGet, apiPost } from './api.js';
import { renderStageMarkers, updateHistoricalCharts } from './charts.js';
import { config, state } from './state.js';
import { escapeHtml, fmtNum, fmtSigned, formatAge, formatDuration } from './util.js';

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

function updateHumidityBar(humidity, targetHumidity) {
    const fillEl = document.getElementById('humidity-bar-fill');
    const targetEl = document.getElementById('humidity-bar-target');
    const diffEl = document.getElementById('humidity-diff');
    
    if (fillEl && humidity !== null && humidity !== undefined) {
        fillEl.style.width = `${Math.min(Math.max(humidity, 0), 100)}%`;
    }
    
    if (targetEl && targetHumidity !== null && targetHumidity !== undefined) {
        targetEl.style.left = `${Math.min(Math.max(targetHumidity, 0), 100)}%`;
    }
    
    // Update difference indicator
    if (diffEl && humidity !== null && targetHumidity !== null) {
        const diff = humidity - targetHumidity;
        const absDiff = Math.abs(diff).toFixed(1);
        
        if (Math.abs(diff) <= 3) {
            diffEl.className = 'humidity-diff on-target';
            diffEl.innerHTML = '✓ En objetivo';
        } else if (diff < 0) {
            diffEl.className = 'humidity-diff too-low';
            diffEl.innerHTML = `↓ ${absDiff}% por debajo`;
        } else {
            diffEl.className = 'humidity-diff too-high';
            diffEl.innerHTML = `↑ ${absDiff}% por encima`;
        }
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

// ===============================
// Indoor sensors
// ===============================
// The tent has two DHT22 on one node: a primary that drives control and a
// witness that drives nothing. The witness exists to catch the primary
// reporting a plausible wrong number, so it is only worth having if somebody
// can see it - which is what this section is.

const SENSOR_ROLES = {
    primary: { icon: '🎯', label: 'Primario · controla' },
    witness: { icon: '👁️', label: 'Testigo · sólo vigila' },
    // Shown and nothing else. The sensor above the lamp is worth reading and
    // useless as a witness: its offset against the primary moves with the
    // photoperiod, not with the sensors' health.
    observer: { icon: '📈', label: 'Observación · sólo informa' },
};

/** Same wording the control loop logs, so the two are recognisably the same fault. */
const DRIFT_FLAG_LABELS = {
    stuck: 'valor congelado',
    railed: 'pegado al tope de escala',
};

function sensorName(sensor) {
    return sensor.sensor_id
        ? `Sensor ${String(sensor.sensor_id).toUpperCase()}`
        : 'Sensor interior';
}

function sensorCardHtml(sensor) {
    const role = SENSOR_ROLES[sensor.role] || { icon: '📍', label: sensor.role || '' };

    // Three states, not two: a sensor that never posted and one whose reading
    // went stale look identical on a card that only knows ok/error, and they
    // call for different things - check the wiring vs. check the network.
    let badge;
    let stateClass;
    if (!sensor.has_reading) {
        badge = '<span class="sensor-badge error">❌ sin datos</span>';
        stateClass = 'is-missing';
    } else if (sensor.is_stale) {
        badge = '<span class="sensor-badge error">⏱️ dato viejo</span>';
        stateClass = 'is-stale';
    } else {
        badge = '<span class="sensor-badge ok">✅ OK</span>';
        stateClass = 'is-ok';
    }

    const flags = (sensor.flags || [])
        .map(flag => `<div class="sensor-flag">⚠️ ${escapeHtml(DRIFT_FLAG_LABELS[flag] || flag)}</div>`)
        .join('');

    return `
        <div class="sensor-card ${stateClass}">
            <div class="sensor-card-header">
                <div class="sensor-card-name">${escapeHtml(sensorName(sensor))}</div>
                ${badge}
            </div>
            <div class="sensor-card-role">${role.icon} ${escapeHtml(role.label)}</div>
            <div class="sensor-card-readings">
                <div>
                    <div class="metric-label">Temperatura</div>
                    <div class="metric-value sensor-temp">${fmtNum(sensor.temperature, 1)}<span class="metric-unit">°C</span></div>
                </div>
                <div>
                    <div class="metric-label">Humedad</div>
                    <div class="metric-value sensor-humidity">${fmtNum(sensor.humidity, 1)}<span class="metric-unit">%</span></div>
                </div>
            </div>
            <div class="sensor-card-footer">
                <span title="VPD del aire a la temperatura del sensor, no de la hoja">VPD aire ${fmtNum(sensor.vpd, 2)} kPa</span>
                <span>${escapeHtml(formatAge(sensor.age_seconds))}</span>
            </div>
            ${flags}
        </div>
    `;
}

function driftVerdictHtml(drift) {
    if (!drift) {
        // sensor_status expires with the rest of the dashboard keys, so no
        // verdict means the control loop is not publishing one.
        return '<div class="sensor-flag">⏳ El lazo de control no publicó un veredicto</div>';
    }
    if (drift.ok) {
        return '<div class="drift-verdict ok">✅ Sin deriva</div>';
    }
    const messages = (drift.messages || [])
        .map(message => `<div class="sensor-flag">⚠️ ${escapeHtml(message)}</div>`)
        .join('');
    return messages || '<div class="sensor-flag">⚠️ Revisar los sensores</div>';
}

function comparisonCardHtml(payload) {
    const delta = payload.delta;
    const drift = payload.drift || null;
    const baseline = drift && drift.baseline;

    // The offset between two sensors in the same tent proves nothing on its own
    // - position and airflow are real. It is the *change* from the learned
    // baseline that means something, so the baseline is shown next to it.
    const baselineText = baseline
        ? `Base: ${fmtSigned(baseline.temperature, 2)} °C / ${fmtSigned(baseline.humidity, 2)} %`
        : 'Base: aprendiendo (~200 muestras)';

    // Styled as a value only when it is one: "sin par de lecturas frescas" set
    // in the big lime numerals reads as a measurement at a glance.
    const deltaText = delta
        ? `${fmtSigned(delta.temperature, 1)} °C · ${fmtSigned(delta.humidity, 1)} %`
        : 'sin par de lecturas frescas';
    const deltaClass = delta ? 'metric-value sensor-delta' : 'sensor-delta-absent';

    return `
        <div class="sensor-card sensor-card-compare">
            <div class="sensor-card-header">
                <div class="sensor-card-name">Diferencia</div>
            </div>
            <div class="sensor-card-role">↔️ testigo − primario</div>
            <div class="${deltaClass}">${escapeHtml(deltaText)}</div>
            <div class="sensor-card-footer"><span>${escapeHtml(baselineText)}</span></div>
            ${driftVerdictHtml(drift)}
        </div>
    `;
}

function updateDriftBadge(drift, hasWitness) {
    const el = document.getElementById('drift-badge');
    if (!el) return;

    if (!hasWitness) {
        // Said out loud on purpose: without a witness the silent-drift failure
        // - a degraded sensor reporting a plausible wrong number - is not being
        // watched by anything, and a blank badge would read as "all good".
        el.className = 'sensor-badge';
        el.textContent = 'sin control de deriva';
    } else if (!drift) {
        el.className = 'sensor-badge';
        el.textContent = '⏳ sin veredicto';
    } else if (drift.ok) {
        el.className = 'sensor-badge ok';
        el.textContent = '✅ sin deriva';
    } else {
        el.className = 'sensor-badge error';
        el.textContent = '⚠️ revisar sensores';
    }
}

function updateIndoorSensors(payload) {
    const grid = document.getElementById('indoor-sensors-grid');
    if (!grid) return;

    const sensors = payload?.sensors || [];
    if (!sensors.length) {
        grid.innerHTML = `
            <div class="sensor-card is-missing">
                <div class="sensor-card-header">
                    <div class="sensor-card-name">Sin sensores configurados</div>
                </div>
            </div>
        `;
        updateDriftBadge(null, false);
        return;
    }

    const cards = sensors.map(sensorCardHtml);
    // Gated on a witness existing, not on the number of sensors: an observer is
    // not something to compare against, and a difference card next to one would
    // claim a cross-check the installation does not have.
    if (payload.has_witness) cards.push(comparisonCardHtml(payload));
    grid.innerHTML = cards.join('');
    updateDriftBadge(payload.drift, payload.has_witness);
}

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
    } else {
        label.textContent = `Últimos ${state.period} día${state.period > 1 ? 's' : ''}`;
    }
}

function fetchPeriodSummary() {
    let url;
    if (state.hours !== null) {
        url = `/api/period-summary?hours=${state.hours}`;
    } else {
        url = `/api/period-summary?days=${state.period}`;
    }
    
    apiGet(url, { key: 'summary' })
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

function updateSensorStatus(data) {
    const indoorEl = document.getElementById('sensor-status-indoor');
    const outdoorEl = document.getElementById('sensor-status-outdoor');
    const alertEl = document.getElementById('sensor-alert');
    const alertMsgEl = document.getElementById('sensor-alert-message');

    let errors = [];

    const updateBadge = (el, ok, error) => {
        if (!el) return;
        if (ok === true) {
            el.textContent = '✅ OK';
            el.className = 'sensor-badge ok';
        } else if (ok === false) {
            el.textContent = '❌ Error';
            el.className = 'sensor-badge error';
        } else {
            el.textContent = '⏳';
            el.className = 'sensor-badge';
        }
    };

    // Only the primary: this is the control loop's own view of the sensor it
    // acts on. Each sensor's individual badge lives in the sensors section,
    // fed by that sensor's own reading.
    if (data.indoor?.ok === true) {
        updateBadge(indoorEl, true);
    } else if (data.indoor?.ok === false) {
        updateBadge(indoorEl, false);
        errors.push('Interior: ' + (data.indoor.error || 'Error'));
    }

    if (data.outdoor?.ok === true) {
        updateBadge(outdoorEl, true);
    } else if (data.outdoor?.ok === false) {
        updateBadge(outdoorEl, false);
        errors.push('Exterior: ' + (data.outdoor.error || 'Error'));
    }

    if (alertEl && alertMsgEl) {
        if (errors.length > 0) {
            alertMsgEl.textContent = errors.join(' • ');
            alertEl.style.display = 'flex';
        } else {
            alertEl.style.display = 'none';
        }
    }
}

// ===============================
// Test mode
// ===============================
// While it is on the control loop stands down: the relays follow what is asked
// here and nothing is written to the history. It ends on its own, which is the
// whole reason it is safe to offer at all.

/** Last known test-mode state, from /api/output-status so it cannot disagree. */
let testMode = { active: false };

function renderTestModeBanner(mode) {
    const banner = document.getElementById('test-mode-banner');
    const enterBtn = document.getElementById('test-mode-enter');
    const countdown = document.getElementById('test-mode-countdown');

    if (banner) banner.classList.toggle('show', !!mode.active);
    // Hidden rather than disabled while active: the way out is the "Salir" on
    // the banner, and two competing controls invite turning it on twice.
    if (enterBtn) enterBtn.style.display = mode.active ? 'none' : '';
    if (countdown) countdown.textContent = formatDuration(mode.remaining_seconds);
}

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
    renderTestModeBanner(testMode);

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
    if (!confirm(
        'El lazo deja de controlar la carpa: los relés van a quedar donde vos los pongas '
        + 'y no se va a guardar nada en el histórico.\n\nVuelve solo a automático a las 8 horas.'
    )) return;

    const result = await apiPost('/api/test-mode', { enabled: true });
    if (!result.ok) {
        alert(`❌ No se pudo entrar en modo prueba: ${result.error}`);
        return;
    }
    fetchOutputsStatus();
}

async function exitTestMode() {
    const result = await apiPost('/api/test-mode', { enabled: false });
    if (!result.ok) {
        alert(`❌ No se pudo salir del modo prueba: ${result.error}`);
        return;
    }
    fetchOutputsStatus();
}

async function setTestOutput(trigger) {
    const name = trigger?.dataset?.name;
    if (!name) return;

    const result = await apiPost('/api/test-mode/outputs', {
        name,
        state: trigger.dataset.state === 'true',
    });
    if (!result.ok) {
        alert(`❌ ${result.error}`);
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

function fetchIndoorSensors() {
    apiGet('/api/sensors/indoor').then(data => { if (data) updateIndoorSensors(data); });
}

function fetchSensorStatus() {
    apiGet('/api/sensor-status').then(data => { if (data) updateSensorStatus(data); });
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
    apiGet(url, { key: 'history' }).then(data => {
        if (!data) return;
        updateHistoricalCharts(hours !== null
            ? { data: data.data || [], count: data.count || 0, aggregated: false }
            : data);
        // The x-axis just changed, so the markers need repositioning.
        if (state.timeline) renderStageMarkers(state.timeline);
    });
}

export {
    updateSummaryPeriodLabel, fetchPeriodSummary, fetchCurrentData, fetchIndoorSensors,
    fetchOutputsStatus, fetchSensorStatus, fetchDatabaseStats, fetchHistoricalData,
    enterTestMode, exitTestMode, setTestOutput,
};
