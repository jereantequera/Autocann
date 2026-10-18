/**
 * The alert boxes above the page. alertDecision (alerts.js) decides which one
 * shows; this module only paints that decision and keeps the anomaly list.
 */

import { alertDecision, anomalyIcon, moreWarningsLabel } from './alerts.js';
import { healthState } from './health-state.js';
import { escapeHtml, icon } from './util.js';

/**
 * Los carteles de arriba de todo, decididos en un solo lugar.
 *
 * El de conexión lo maneja api.js; éste es el de sensores y el de anomalías,
 * y los dos salen de alertDecision para que nunca se muestren juntos diciendo
 * lo mismo.
 */
export function renderAlerts() {
    const decision = alertDecision({
        health: healthState.snapshot,
        anomalies: healthState.anomalyItems,
        dismissed: healthState.anomaliesDismissed,
    });

    const broken = document.getElementById('sensor-alert');
    const brokenMessage = document.getElementById('sensor-alert-message');
    const brokenTitle = document.getElementById('sensor-alert-title');
    const brokenMore = document.getElementById('sensor-alert-more');
    if (broken) {
        const show = decision.mode === 'broken';
        broken.classList.toggle('show', show);
        if (show) {
            if (brokenTitle) brokenTitle.textContent = decision.title;
            if (brokenMessage) brokenMessage.textContent = decision.text;
            if (brokenMore) {
                brokenMore.hidden = !decision.more;
                brokenMore.textContent = moreWarningsLabel(decision.more);
            }
        }
    }

    const notice = document.getElementById('grow-notice');
    if (notice) {
        notice.classList.toggle('show', !!decision.notice);
        const text = document.getElementById('grow-notice-text');
        if (decision.notice && text) text.textContent = decision.notice.text;
    }

    const warnings = document.getElementById('anomalies-alert');
    const list = document.getElementById('anomalies-list');
    const title = document.getElementById('anomalies-title-text');
    const iconEl = document.getElementById('anomalies-icon');
    const showWarnings = decision.mode === 'warnings';
    if (warnings) warnings.classList.toggle('show', showWarnings);
    if (showWarnings) {
        if (warnings) {
            warnings.classList.toggle('alert-box--warn', decision.tone === 'warn');
            warnings.classList.toggle('alert-box--info', decision.tone === 'info');
        }
        if (title) title.textContent = decision.title;
        if (iconEl) iconEl.innerHTML = icon(decision.icon);
        if (list) {
            list.innerHTML = decision.items.map(issue => `
                <div class="anomaly-item ${escapeHtml(issue.severity || 'warning')}">
                    ${icon(anomalyIcon(issue.severity), 'anomaly-icon')}
                    <span class="anomaly-text">${escapeHtml(issue.message)}</span>
                    <span class="anomaly-time">${escapeHtml(issue.timestamp ? String(issue.timestamp).split(' ')[1] || '' : '')}</span>
                </div>`).join('')
                + (decision.hidden ? `<div class="anomaly-more">+ ${decision.hidden} más</div>` : '');
        }
    }
}

/**
 * Lo que analytics.js trae de /api/anomalies, para decidir junto con la salud.
 *
 * `dismissed` llega de analytics.js, que es quien sabe si el usuario lo
 * descartó y hasta cuándo.
 */
export function setAnomalies(items, { dismissed = false } = {}) {
    healthState.anomalyItems = Array.isArray(items) ? items : [];
    healthState.anomaliesDismissed = !!dismissed;
    renderAlerts();
}
