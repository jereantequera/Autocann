/**
 * Sistema: calibration, connectivity and the database.
 *
 * Calibration has had a validated endpoint on both verbs since the backend
 * audit and no way to reach it from the browser. The offsets correct every
 * reading the rest of the app shows, so editing them with curl meant the one
 * knob nobody could see was the one that moves all the numbers.
 */

import { apiGet, apiPost, connection } from './api.js';
import { escapeHtml, fmtNum, fmtSigned } from './util.js';

/**
 * Stored offsets by sensor id.
 *
 * /api/calibration answers with a list of rows, not a map; keying it here is
 * what lets a card find its own sensor instead of whatever row happened to be
 * in that position.
 */
let calibration = {};
/** Sensors seen posting, so a never-calibrated one still gets a row. */
let knownSensors = [];
/**
 * Sensors saved in this page session.
 *
 * The caveat that matters - saved is not applied until the loop restarts - was
 * written into the card and then wiped two hundred milliseconds later by the
 * re-render that confirmed the save. It stays until the page is reloaded, which
 * is what restarting the loop leads to anyway.
 */
const savedThisSession = new Set();

// ---------------------------------------------------------------------------
// Calibration
// ---------------------------------------------------------------------------

/** "a" reads as a label, "dht22_outdoor" reads as itself. */
function sensorLabel(sensorId) {
    return sensorId.length <= 2 ? `Sensor ${sensorId.toUpperCase()}` : sensorId;
}

function calibrationRowHtml(sensorId) {
    const current = calibration[sensorId] || {};
    const id = escapeHtml(sensorId);
    const temperature = Number(current.temperature_offset) || 0;
    const humidity = Number(current.humidity_offset) || 0;
    const set = temperature !== 0 || humidity !== 0;
    // An offset stored against an id nothing posts under any more corrects
    // nothing, and looks exactly like one that works until it is said.
    const orphan = !knownSensors.includes(sensorId);
    const saved = savedThisSession.has(sensorId);

    return `
        <div class="calib-card${set ? ' is-set' : ''}${orphan ? ' is-orphan' : ''}" data-sensor="${id}">
            <div class="calib-head">
                <span class="calib-name">${escapeHtml(sensorLabel(sensorId))}</span>
                <span class="calib-state">${set
                    ? `${escapeHtml(fmtSigned(temperature, 2))} °C / ${escapeHtml(fmtSigned(humidity, 2))} %`
                    : 'sin corrección'}</span>
            </div>
            ${orphan ? '<div class="calib-orphan">⚠️ Ningún sensor está posteando con este id, '
                + 'así que esta corrección no se le aplica a nada.</div>' : ''}
            <div class="calib-fields">
                <label>
                    Offset temperatura (°C)
                    <input type="number" step="0.05" min="-20" max="20" inputmode="decimal"
                           data-calib="temperature" value="${fmtNum(temperature, 2, '0')}">
                </label>
                <label>
                    Offset humedad (%)
                    <input type="number" step="0.5" min="-50" max="50" inputmode="decimal"
                           data-calib="humidity" value="${fmtNum(humidity, 2, '0')}">
                </label>
            </div>
            <label class="calib-notes">
                Nota
                <input type="text" data-calib="notes" maxlength="200"
                       placeholder="Contra el termómetro de referencia, 2026-10-12"
                       value="${escapeHtml(current.notes || '')}">
            </label>
            <div class="calib-actions">
                <button class="btn btn-primary btn-mini" data-calib-save="${id}">Guardar</button>
                <button class="btn btn-ghost btn-mini" data-calib-zero="${id}">Poner en 0</button>
                <span class="calib-msg${saved ? ' is-ok' : ''}">${saved
                    ? 'Guardado · se aplica cuando reinicies el lazo' : ''}</span>
            </div>
        </div>`;
}

function renderCalibration() {
    const grid = document.getElementById('calibration-grid');
    const badge = document.getElementById('calibration-badge');
    if (!grid) return;

    // Posting sensors first: the ones somebody came here to adjust. Ids that
    // only exist in the calibration table go last, flagged.
    const live = [...knownSensors].sort();
    const orphans = Object.keys(calibration).filter(id => !live.includes(id)).sort();
    const sensors = [...live, ...orphans];
    if (!sensors.length) {
        grid.innerHTML = '<div class="sys-note">Ningún sensor posteó todavía, '
            + 'así que no hay nada que calibrar.</div>';
        if (badge) { badge.className = 'sensor-badge'; badge.textContent = 'sin sensores'; }
        return;
    }

    grid.innerHTML = sensors.map(calibrationRowHtml).join('');

    if (badge) {
        const corrected = sensors.filter(id => {
            const row = calibration[id];
            return row && (Number(row.temperature_offset) || Number(row.humidity_offset));
        }).length;
        badge.className = corrected ? 'sensor-badge ok' : 'sensor-badge';
        badge.textContent = corrected
            ? `${corrected} con corrección`
            : 'todos en 0';
    }
}

async function saveCalibration(card, { zero = false } = {}) {
    const sensorId = card.dataset.sensor;
    const message = card.querySelector('.calib-msg');
    const field = name => card.querySelector(`[data-calib="${name}"]`);

    if (zero) {
        field('temperature').value = '0';
        field('humidity').value = '0';
    }

    const body = {
        sensor_id: sensorId,
        temperature_offset: Number(field('temperature').value) || 0,
        humidity_offset: Number(field('humidity').value) || 0,
        notes: field('notes').value.trim() || null,
    };

    message.textContent = 'Guardando…';
    const result = await apiPost('/api/calibration', body);
    if (!result.ok) {
        message.textContent = result.error;
        message.className = 'calib-msg is-error';
        return;
    }
    // Said out loud and left there: the loop reads the offsets once at startup,
    // so a saved value that is not yet in effect would otherwise look applied.
    savedThisSession.add(sensorId);
    fetchCalibration();
}

function fetchCalibration() {
    apiGet('/api/calibration', { key: 'calibration' }).then(data => {
        if (!data || !Array.isArray(data.calibration)) return;
        calibration = Object.fromEntries(
            data.calibration.filter(row => row && row.sensor_id).map(row => [row.sensor_id, row]));
        renderCalibration();
    });
}

/**
 * Which sensors exist at all.
 *
 * Asked separately from the offsets because a sensor that has never been
 * corrected has no calibration row, and that is exactly the sensor somebody
 * opening this view wants a form for.
 */
function fetchKnownSensors() {
    apiGet('/api/sensors/indoor', { key: 'sensors-system' }).then(data => {
        const ids = (data?.sensors || []).map(sensor => sensor.sensor_id).filter(Boolean);
        if (!ids.length) return;
        knownSensors = [...new Set([...knownSensors, ...ids])];
        renderCalibration();
    });
}

// ---------------------------------------------------------------------------
// Connectivity
// ---------------------------------------------------------------------------

function statusCardHtml(title, ok, detail) {
    const state = ok === true ? 'ok' : ok === false ? 'error' : '';
    const text = ok === true ? '✅ OK' : ok === false ? '❌ Error' : '⏳ Desconocido';
    return `
        <div class="sys-card">
            <div class="metric-label">${escapeHtml(title)}</div>
            <div class="sys-state ${state}">${text}</div>
            <div class="sys-detail">${escapeHtml(detail || '')}</div>
        </div>`;
}

function renderConnectivity(status) {
    const grid = document.getElementById('connectivity-grid');
    if (!grid) return;

    grid.innerHTML = [
        statusCardHtml('Backend', !connection.offline,
            connection.lastSuccessAt
                ? `Última respuesta ${new Date(connection.lastSuccessAt).toLocaleTimeString('es-AR', { hour12: false })}`
                : 'Sin respuestas todavía'),
        statusCardHtml('Sensor interior', status?.indoor?.ok ?? null,
            status?.indoor?.error || 'Primario del lazo de control'),
        statusCardHtml('Sensor exterior', status?.outdoor?.ok ?? null,
            status?.outdoor?.error || 'Referencia de afuera'),
    ].join('');
}

function fetchConnectivity() {
    apiGet('/api/sensor-status', { key: 'sensor-status-system' })
        .then(data => renderConnectivity(data));
}

// ---------------------------------------------------------------------------
// Database
// ---------------------------------------------------------------------------

function renderDatabase(stats) {
    const set = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    };
    const count = value => (Number.isFinite(value) ? value.toLocaleString('es-AR') : '--');

    set('stat-db-size', stats.database_size_mb !== undefined ? `${stats.database_size_mb} MB` : '--');
    set('stat-grows', count(stats.grow_count));
    set('stat-readings', count(stats.sensor_data_count));
    set('stat-events', count(stats.control_events_count));
    set('stat-span', stats.oldest_record
        ? `${stats.oldest_record.split(' ')[0]} → ${(stats.newest_record || '').split(' ')[0]}`
        : 'sin lecturas');
}

function fetchDatabase() {
    // Not polled: the file grows by kilobytes an hour, and nobody watches this
    // card waiting for it to move.
    apiGet('/api/database-stats', { key: 'database-stats' })
        .then(data => { if (data) renderDatabase(data); });
}

// ---------------------------------------------------------------------------
// Session
// ---------------------------------------------------------------------------
// Only shown where there is a PIN to log out of. `/api/auth` answers whether
// one is configured even when it is not, so the dashboard reads the
// configuration instead of inferring it from a 401 it would never see while
// logged in.

function fetchSession() {
    apiGet('/api/auth', { key: 'auth' }).then(data => {
        const box = document.getElementById('session-section');
        if (box && data) box.hidden = !data.pin_required;
    });
}

async function logout() {
    const result = await apiPost('/api/logout', {});
    // Reload either way: on success the next request answers 401 and lands on
    // the login page, and on failure the page should show whatever is actually
    // true rather than a button that appeared to do nothing.
    window.location.reload();
    return result;
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function refreshSystem() {
    fetchKnownSensors();
    fetchCalibration();
    fetchConnectivity();
    fetchDatabase();
    fetchSession();
}

function wireSystem() {
    // `.view` is not decoration here: the nav tab carries the same data-view and
    // comes first in the document, so a bare attribute selector binds every one
    // of these buttons to the anchor instead of the section.
    const view = document.querySelector('.view[data-view="sistema"]');
    if (!view) return;

    view.addEventListener('click', event => {
        if (event.target.closest('[data-sys="refresh"]')) {
            refreshSystem();
            return;
        }
        if (event.target.closest('[data-sys="logout"]')) {
            logout();
            return;
        }
        const save = event.target.closest('[data-calib-save]');
        if (save) {
            saveCalibration(save.closest('.calib-card'));
            return;
        }
        const zero = event.target.closest('[data-calib-zero]');
        if (zero) saveCalibration(zero.closest('.calib-card'), { zero: true });
    });

    // Only on entering: none of this moves on its own, and the view is opened
    // to change something rather than to watch it.
    document.addEventListener('view:enter', event => {
        if (event.detail?.view === 'sistema') refreshSystem();
    });
}

export { fetchCalibration, fetchSession, logout, refreshSystem, wireSystem };
