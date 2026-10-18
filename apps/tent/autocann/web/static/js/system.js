/**
 * Sistema: calibration, the installation's sensors, the database and the screen.
 *
 * Calibration has had a validated endpoint on both verbs since the backend
 * audit and no way to reach it from the browser. The offsets correct every
 * reading the rest of the app shows, so editing them with curl meant the one
 * knob nobody could see was the one that moves all the numbers.
 *
 * The sensor table is two writers in one row: health.js writes the readings
 * every ten seconds, and this module writes the correction cells once per
 * calibration version. The second never happens on the poll, so a value that
 * is being typed is not wiped by a reading arriving.
 */

import { fetchHealth } from './health.js';
import {
    calibrationIds, changedCalibrations, fetchCalibration, renderCalibrationRows, sensorLabel,
} from './system-calibration.js';
import { saveAll, zeroRow } from './system-save.js';
import {
    fetchDatabase, fetchSession, jumpTo, logout, paintThemeChoice,
} from './system-sections.js';
import { applyTheme, readTheme } from './theme.js';

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function refreshSystem() {
    fetchHealth();
    fetchCalibration();
    fetchDatabase();
    fetchSession();
}

function wireSystem() {
    // `.view` is not decoration here: the nav tab carries the same data-view and
    // comes first in the document, so a bare attribute selector binds every one
    // of these buttons to the anchor instead of the section.
    const view = document.querySelector('.view[data-view="sistema"]');
    if (!view) return;

    paintThemeChoice(readTheme());

    view.addEventListener('click', event => {
        const jump = event.target.closest('[data-sys-jump]');
        if (jump) {
            // Preventing the default is the point: an anchor here would change
            // the hash, and the router would read it as a view and leave Sistema.
            event.preventDefault();
            jumpTo(jump.dataset.sysJump, view);
            return;
        }
        const theme = event.target.closest('[data-theme-choice]');
        if (theme) {
            paintThemeChoice(applyTheme(theme.dataset.themeChoice));
            return;
        }
        if (event.target.closest('[data-sys="refresh"]')) {
            refreshSystem();
            return;
        }
        if (event.target.closest('[data-sys="logout"]')) {
            logout();
            return;
        }
        if (event.target.closest('#calibration-save')) {
            saveAll();
            return;
        }
        const zero = event.target.closest('[data-calib-zero]');
        if (zero) zeroRow(zero.dataset.calibZero);
    });

    // Marks a row as being typed in, so a calibration fetch leaves it alone.
    view.addEventListener('input', event => {
        const row = event.target.closest('#indoor-sensors-grid tr[data-sensor-row]');
        if (row) row.classList.add('is-dirty');
    });

    // Only on entering: none of this moves on its own, and the view is opened
    // to change something rather than to watch it.
    document.addEventListener('view:enter', event => {
        if (event.detail?.view === 'sistema') refreshSystem();
    });
    // The rows exist once health has answered; the correction cells follow it.
    document.addEventListener('health:updated', renderCalibrationRows);
}

export {
    calibrationIds, changedCalibrations, fetchCalibration, fetchSession, logout, refreshSystem,
    sensorLabel, wireSystem,
};
