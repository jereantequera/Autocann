import { apiGet } from './api.js';
import { renderStageMarkers, updateHistoricalCharts } from './charts.js';
import { config, state } from './state.js';
import { escapeHtml, fmtNum } from './util.js';

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
    const indoorEl2 = document.getElementById('sensor-status-indoor-2');
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

    if (data.indoor?.ok === true) {
        updateBadge(indoorEl, true);
        updateBadge(indoorEl2, true);
    } else if (data.indoor?.ok === false) {
        updateBadge(indoorEl, false);
        updateBadge(indoorEl2, false);
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

function updateOutputsStatus(payload) {
    const grid = document.getElementById('outputs-grid');
    const outputs = payload?.outputs || [];

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

    grid.innerHTML = outputs.map(o => {
        let stateText = '--';
        let stateClass = 'unknown';
        if (o.state === true) {
            stateText = 'ENCENDIDA';
            stateClass = 'on';
        } else if (o.state === false) {
            stateText = 'APAGADA';
            stateClass = 'off';
        }

        const label = escapeHtml(o.label || o.name || 'Salida');
        const pin = (o.pin_bcm !== undefined && o.pin_bcm !== null)
            ? `BCM ${Number(o.pin_bcm)}` : '';
        
        return `
            <div class="output-card">
                <div class="output-header">
                    <div class="output-name">${label}</div>
                    <div class="output-pin">${pin}</div>
                </div>
                <div class="output-state ${stateClass}">${stateText}</div>
            </div>
        `;
    }).join('');
}

// API Fetch functions

function fetchCurrentData() {
    apiGet('/api/current-data').then(data => { if (data) updateCurrentData(data); });
}

function fetchOutputsStatus() {
    apiGet('/api/output-status').then(data => { if (data) updateOutputsStatus(data); });
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

export { updateSummaryPeriodLabel, fetchPeriodSummary, fetchCurrentData, fetchOutputsStatus, fetchSensorStatus, fetchDatabaseStats, fetchHistoricalData };
