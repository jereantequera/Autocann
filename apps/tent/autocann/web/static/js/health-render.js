/**
 * Paints each /api/health answer: the checks row and the verdict, then the
 * sensor table, the alert boxes and the sidebar's timestamp. Polls the endpoint.
 */

import { apiGet, connection } from './api.js';
import { renderAlerts } from './health-alerts.js';
import { healthChecks, sensorsBadge, verdictFor } from './health-model.js';
import { renderSensors, setBadge, setOutdoorRow } from './health-sensors.js';
import { healthState } from './health-state.js';
import { escapeHtml, formatAge, stampLastUpdate } from './util.js';

function renderChecks(health) {
    const row = document.getElementById('health-checks');
    const verdict = document.getElementById('health-verdict');
    const age = document.getElementById('health-age');
    const checks = healthChecks(health, { offline: connection.offline });

    if (row) {
        row.innerHTML = checks.map(check => `
            <div class="health-check is-${check.level}">
                <div class="health-check-name">
                    <span class="ac-dot ${check.level === 'ok' ? 'ac-dot--ok' : check.level === 'warn' ? 'ac-dot--warn'
                        : check.level === 'error' ? 'ac-dot--alert' : 'ac-dot--idle'}"></span>
                    ${escapeHtml(check.name)}
                </div>
                <div class="health-check-detail">${escapeHtml(check.detail)}</div>
            </div>`).join('');
    }
    if (verdict) verdict.textContent = verdictFor(health, checks);
    if (age) {
        age.textContent = health?.generated_at
            ? `Revisado ${formatAge(Math.max(0, Date.now() / 1000 - health.generated_at))}`
            : '';
    }
}

function renderHealth(health) {
    healthState.snapshot = health;

    renderSensors(health);
    renderChecks(health);

    // The section badge over the cards in Sistema, the only place sensors are
    // shown now that the panel reads the loop and the sensors from its status.
    setBadge('sensors-badge', sensorsBadge(health.sensors));

    const outdoor = health.outdoor || {};
    setOutdoorRow(outdoor);
    setBadge('sensor-status-outdoor',
        outdoor.ok === true ? { icon: 'circle-check', text: 'OK', state: 'ok' }
            : outdoor.ok === false ? { icon: 'circle-x', text: 'Error', state: 'error' }
                : { icon: 'hourglass', text: '', state: '' });

    renderAlerts();

    // The panel's status line reads the loop's state from this same answer,
    // so it is told when a new one lands rather than polling for it.
    document.dispatchEvent(new CustomEvent('health:updated'));
}

export function fetchHealth() {
    apiGet('/api/health', { key: 'health' }).then(data => {
        if (!data || data.error) return;
        renderHealth(data);
        // This poller runs on every view, so it is the one that keeps the
        // sidebar's "última actualización" honest where current data does not run.
        stampLastUpdate();
    });
}

// The header of Sistema says whether the backend answers; the checks have to
// change with it, not on the next health poll ten seconds later.
// Guarded because the unit tests import this module into Node, which has no
// document; in the page there always is one.
if (typeof document !== 'undefined') {
    document.addEventListener('connection:changed', () => {
        renderChecks(healthState.snapshot);
        renderAlerts();
    });
}
