/**
 * The alert boxes above the page. alertDecision (alerts.js) decides which one
 * shows; this module only paints that decision and keeps the anomaly list.
 */

import {
    alertDecision, anomalyIcon, isUnread, moreWarningsLabel, seenLabel, seenStateLabel, unreadCount,
} from './alerts.js';
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
        showSeen: healthState.showSeen,
    });
    renderBell(unreadCount(healthState.anomalyItems));

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
            const seenToggle = seenLabel(decision.seen, decision.showSeen);
            list.innerHTML = decision.items.map(anomalyRow).join('')
                + (decision.hidden ? `<div class="anomaly-more">+ ${decision.hidden} más</div>` : '')
                + (seenToggle
                    ? `<button type="button" class="anomaly-seen-toggle" data-action="toggle-seen-alerts">${escapeHtml(seenToggle)}</button>`
                    : '');
        }
    }
}

/**
 * One alert: what happened, when, and what to do with it. News gets "Visto"
 * and "Posponer"; a seen one says so instead, dimmed, so the list opened from
 * the bell reads as history rather than as a second set of alarms.
 */
function anomalyRow(issue) {
    const unread = isUnread(issue);
    const time = issue.timestamp ? String(issue.timestamp).split(' ')[1] || '' : '';
    const id = Number.isInteger(issue.id) ? issue.id : null;
    const actions = id === null ? '' : unread
        ? `<span class="anomaly-actions">
               <button type="button" class="ac-btn ac-btn--ghost ac-btn--sm" data-action="ack-alert" data-alert-id="${id}">Visto</button>
               <button type="button" class="ac-btn ac-btn--ghost ac-btn--sm" data-action="snooze-alert" data-alert-id="${id}" title="Ocultarla por una hora">Posponer 1 h</button>
           </span>`
        : `<span class="anomaly-state">${escapeHtml(seenStateLabel(issue))}</span>`;
    return `
        <div class="anomaly-item ${escapeHtml(issue.severity || 'warning')}${unread ? '' : ' is-seen'}">
            ${icon(anomalyIcon(issue.severity), 'anomaly-icon')}
            <span class="anomaly-text">${escapeHtml(issue.message)}</span>
            <span class="anomaly-time">${escapeHtml(time)}</span>
            ${actions}
        </div>`;
}

/** The bell's badge: the alerts nobody has seen yet, on every view. */
function renderBell(count) {
    const bell = document.getElementById('alerts-bell');
    const badge = document.getElementById('alerts-bell-count');
    if (!bell || !badge) return;
    badge.hidden = !count;
    badge.textContent = count > 9 ? '9+' : String(count);
    bell.classList.toggle('has-unread', count > 0);
    bell.setAttribute('aria-label', count
        ? `Alertas: ${count} sin ver` : 'Alertas: nada sin ver');
}

/**
 * Lo que analytics.js trae de /api/alerts, para decidir junto con la salud.
 * Cada alerta trae si ya se vio: eso lo guarda el backend, no esta página.
 */
export function setAnomalies(items) {
    healthState.anomalyItems = Array.isArray(items) ? items : [];
    renderAlerts();
}

/** Show or hide the seen alerts too (the bell, or the "N vistas" line). */
export function setShowSeen(show) {
    healthState.showSeen = !!show;
    renderAlerts();
}
