/**
 * Saving calibration: the rows that differ from the server go out one request
 * each, and "Poner en 0" saves a single sensor at zero.
 */

import { apiPost } from './api.js';
import { errorMessage } from './errors.js';
import {
    changedCalibrations, fetchCalibration, readRow, sensorLabel, sensorRows,
} from './system-calibration.js';
import { systemState } from './system-state.js';

// ---------------------------------------------------------------------------
// Saving
// ---------------------------------------------------------------------------

/**
 * Saves the rows that differ from the server, one request each.
 *
 * Rows that did not change are not sent: a save that rewrites every sensor
 * says "saved" about values nobody touched.
 */
async function saveChanged(rows, message) {
    if (!rows.length) {
        message.textContent = 'Nada cambió desde la última vez.';
        message.className = 'sys-note-inline';
        return;
    }
    message.textContent = 'Guardando…';
    message.className = 'sys-note-inline';

    for (const row of rows) {
        const body = {
            sensor_id: row.sensorId,
            temperature_offset: row.temperature,
            humidity_offset: row.humidity,
            notes: row.notes,
        };
        const result = await apiPost('/api/calibration', body);
        if (!result.ok) {
            message.textContent = `${sensorLabel(row.sensorId)}: ${errorMessage(result)}`;
            message.className = 'sys-note-inline is-error';
            return;
        }
        systemState.savedThisSession.add(row.sensorId);
    }

    // Said out loud and left there: the loop reads the offsets once at startup,
    // so a saved value that is not yet in effect would otherwise look applied.
    message.textContent = 'Guardado · se aplica cuando reinicies el lazo de control';
    message.className = 'sys-note-inline is-ok';
    fetchCalibration();
}

export function saveAll() {
    const rows = sensorRows()
        .filter(row => row.querySelector('[data-calib="temperature"]'))
        .map(readRow);
    saveChanged(changedCalibrations(rows, systemState.calibration), document.getElementById('calibration-msg'));
}

/** "Poner en 0" saves that one sensor at zero, as it always did: the button says what it does. */
export function zeroRow(sensorId) {
    const row = sensorRows().find(other => other.dataset.sensorRow === sensorId);
    if (!row) return;
    row.querySelector('[data-calib="temperature"]').value = '0';
    row.querySelector('[data-calib="humidity"]').value = '0';
    saveChanged(changedCalibrations([readRow(row)], systemState.calibration),
        document.getElementById('calibration-msg'));
}
