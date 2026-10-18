/**
 * Calibration in the sensor table: the sensor labels, the diff against the
 * server, the correction cells and orphan rows, and the fetch that brings the
 * stored offsets in.
 */

import { apiGet } from './api.js';
import { latestHealth } from './health.js';
import { systemState } from './system-state.js';
import { escapeHtml, fmtNum } from './util.js';

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/** "a" reads as a label, "dht22_outdoor" reads as itself. */
export function sensorLabel(sensorId) {
    return sensorId.length <= 2 ? `Sensor ${sensorId.toUpperCase()}` : sensorId;
}

/**
 * Which of the rows differ from what the server holds.
 *
 * Compared as numbers and trimmed text: the field is a string, the stored value
 * is a number, and "0" against 0 is not a change.
 */
export function changedCalibrations(rows, stored) {
    return rows.filter(row => {
        const saved = stored[row.sensorId] || {};
        return Number(row.temperature) !== (Number(saved.temperature_offset) || 0)
            || Number(row.humidity) !== (Number(saved.humidity_offset) || 0)
            || (row.notes || null) !== (saved.notes || null);
    });
}

/** Ids a calibration row could be about: the posting sensors, then the ones only the table knows. */
export function calibrationIds(live, stored) {
    const orphans = Object.keys(stored).filter(id => !live.includes(id)).sort();
    return { live: [...live].sort(), orphans };
}

// ---------------------------------------------------------------------------
// The table
// ---------------------------------------------------------------------------

export function sensorRows() {
    return [...(document.getElementById('indoor-sensors-grid')?.rows || [])];
}

/** The rows health.js writes: the sensors, not the outdoor reference or an orphan. */
function liveSensorIds() {
    return sensorRows()
        .filter(row => row.dataset.sensorRow && row.dataset.sensorRow !== 'outdoor'
            && !row.hasAttribute('data-orphan'))
        .map(row => row.dataset.sensorRow);
}

function calibrationCellsHtml(sensorId, current) {
    const id = escapeHtml(sensorId);
    const temperature = Number(current.temperature_offset) || 0;
    const humidity = Number(current.humidity_offset) || 0;
    return {
        temperature: `<input class="ac-field__input ac-field__input--num" type="number" step="0.05"
            min="-20" max="20" inputmode="decimal" data-calib="temperature"
            aria-label="Corrección de temperatura del sensor ${id}"
            value="${fmtNum(temperature, 2, '0')}">`,
        humidity: `<input class="ac-field__input ac-field__input--num" type="number" step="0.5"
            min="-50" max="50" inputmode="decimal" data-calib="humidity"
            aria-label="Corrección de humedad del sensor ${id}"
            value="${fmtNum(humidity, 2, '0')}">`,
        notes: `<div class="calib-note">
            <input class="ac-field__input" type="text" data-calib="notes" maxlength="200"
                aria-label="Nota de la corrección del sensor ${id}"
                placeholder="Contra el termómetro de referencia"
                value="${escapeHtml(current.notes || '')}">
            <button type="button" class="ac-btn ac-btn--ghost" data-calib-zero="${id}">Poner en 0</button>
        </div>`,
    };
}

/** Fills the three correction cells of a row, once per calibration version. */
function fillCalibrationRow(row, sensorId) {
    const cells = calibrationCellsHtml(sensorId, systemState.calibration[sensorId] || {});
    for (const [field, html] of Object.entries(cells)) {
        const cell = row.querySelector(`[data-calib-cell="${field}"]`);
        if (cell) cell.innerHTML = html;
    }
    row.dataset.calibVersion = String(systemState.calibrationVersion);
    row.classList.toggle('is-set', (Number(systemState.calibration[sensorId]?.temperature_offset) || 0) !== 0
        || (Number(systemState.calibration[sensorId]?.humidity_offset) || 0) !== 0);
    row.classList.remove('is-dirty');
}

function orphanRowHtml(sensorId) {
    // An offset stored against an id nothing posts under any more corrects
    // nothing, and looks exactly like one that works until it is said. The row
    // keeps the table's shape, and says so in the place a reading would be.
    return `
        <td class="sensor-name">
            <span class="sensor-name-line"><span class="ac-dot ac-dot--idle"></span><span>${escapeHtml(sensorLabel(sensorId))}</span></span>
            <span class="sensor-why"><span>Ningún sensor está posteando con este id: esta corrección no se le aplica a nada.</span></span>
        </td>
        <td data-label="Rol">—</td>
        <td class="ac-num" data-label="Temp.">—</td>
        <td class="ac-num" data-label="Humedad">—</td>
        <td class="ac-num" data-label="Última">—</td>
        <td data-calib-cell="temperature" data-label="Corrección °C"></td>
        <td data-calib-cell="humidity" data-label="Corrección %"></td>
        <td data-calib-cell="notes" data-label="Nota"></td>`;
}

/**
 * Brings every correction cell up to the current calibration.
 *
 * Rows the user is editing (`is-dirty`) are left alone: a fetch that lands
 * mid-typing must not erase what was typed.
 */
export function renderCalibrationRows() {
    const tbody = document.getElementById('indoor-sensors-grid');
    if (!tbody) return;

    const health = latestHealth();
    for (const row of sensorRows()) {
        const id = row.dataset.sensorRow;
        if (!id || id === 'outdoor') continue;
        if (row.classList.contains('is-dirty')) continue;
        if (row.dataset.calibVersion === String(systemState.calibrationVersion)) continue;
        fillCalibrationRow(row, id);
    }

    // An orphan is only a claim once health has said which sensors exist: before
    // that, every stored id would look like one.
    if (!health) return;
    const live = liveSensorIds();
    const { orphans } = calibrationIds(live, systemState.calibration);
    for (const id of orphans) {
        let row = sensorRows().find(other => other.dataset.sensorRow === id);
        if (!row) {
            row = document.createElement('tr');
            row.dataset.sensorRow = id;
            row.dataset.orphan = '';
            row.innerHTML = orphanRowHtml(id);
            tbody.appendChild(row);
        }
        if (row.dataset.calibVersion !== String(systemState.calibrationVersion)
            && !row.classList.contains('is-dirty')) {
            fillCalibrationRow(row, id);
        }
    }
}

export function renderCalibrationBadge() {
    const badge = document.getElementById('calibration-badge');
    if (!badge) return;
    const corrected = Object.values(systemState.calibration).filter(row =>
        Number(row.temperature_offset) || Number(row.humidity_offset)).length;
    badge.className = corrected ? 'sensor-badge ok' : 'sensor-badge';
    badge.textContent = corrected ? `${corrected} con corrección` : 'todos en 0';
}

/** The saved values of one row, read from its inputs. */
export function readRow(row) {
    const field = name => row.querySelector(`[data-calib="${name}"]`);
    const number = input => Number(input?.value) || 0;
    return {
        sensorId: row.dataset.sensorRow,
        temperature: number(field('temperature')),
        humidity: number(field('humidity')),
        notes: field('notes')?.value.trim() || null,
    };
}

export function fetchCalibration() {
    apiGet('/api/calibration', { key: 'calibration' }).then(data => {
        if (!data || !Array.isArray(data.calibration)) return;
        systemState.calibration = Object.fromEntries(
            data.calibration.filter(row => row && row.sensor_id).map(row => [row.sensor_id, row]));
        systemState.calibrationVersion += 1;
        renderCalibrationRows();
        renderCalibrationBadge();
        if (systemState.savedThisSession.size) {
            const message = document.getElementById('calibration-msg');
            if (message && !message.textContent) {
                message.textContent = 'Guardado · se aplica cuando reinicies el lazo de control';
                message.className = 'sys-note-inline is-ok';
            }
        }
    });
}
