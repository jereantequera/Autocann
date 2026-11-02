import { apiGet, apiPost } from './api.js';
import { setAnomalies, setShowSeen } from './health-alerts.js';
import { healthState } from './health-state.js';
import { toast } from './modals.js';
import { navigate } from './views.js';
import { fetchTimeInRange } from './dashboard.js';
import {
    dayName, hourBar, hourlySlots, parseIsoDay, scoreTone, trendText, weekLabel,
} from './stats-math.js';
import { escapeHtml, fmtComma, icon } from './util.js';

/** The anomaly banner, the time-in-range entry point and the weekly report. */

/**
 * The VPD score card is gone: the readout that answers the same question for
 * the selected period lives in the stats header now, and it is computed in
 * dashboard.js. Kept as an entry point because the timers and the grow filter
 * still ask for it by name.
 */
function fetchVpdScore() {
    fetchTimeInRange();
}

/** The last list the server sent, kept so a toggle can re-draw without refetching. */
let anomalyItems = [];

/**
 * The anomalies are drawn by health.js, not here: when the installation is
 * broken they fold into that one alert as a count, and only when it is fine do
 * they get their own box. This module fetches them and sends what the grower
 * does with each one ("Visto", "Posponer") to the backend, which remembers it
 * for every device.
 */
function updateAnomalies(data) {
    if (data.error) return;
    // The severity is the backend's; an item that carries none is a warning.
    anomalyItems = (data.items || []).map(item => ({ ...item, severity: item.severity || 'warning' }));
    setAnomalies(anomalyItems);
}

function fetchAnomalies() {
    apiGet('/api/alerts').then(data => { if (data) updateAnomalies(data); });
}

/**
 * Hide one alert on the spot, then tell the backend and take its answer.
 * Hidden first so the button answers the press; a refusal puts it back.
 */
async function sendAlertAction(trigger, path, body) {
    const id = Number(trigger?.dataset?.alertId);
    if (!Number.isInteger(id)) return;
    anomalyItems = anomalyItems.map(item => (item.id === id ? { ...item, unread: false } : item));
    setAnomalies(anomalyItems);
    const result = await apiPost(`/api/alerts/${id}/${path}`, body);
    if (!result.ok) toast('No se pudo guardar. Probá de nuevo.', { kind: 'error' });
    fetchAnomalies();
}

function ackAlert(trigger) {
    return sendAlertAction(trigger, 'ack');
}

/** One hour: long enough to deal with it, short enough not to forget it. */
const SNOOZE_MINUTES = 60;

function snoozeAlert(trigger) {
    return sendAlertAction(trigger, 'snooze', { minutes: SNOOZE_MINUTES });
}

function toggleSeenAlerts() {
    setShowSeen(!healthState.showSeen);
}

/**
 * The bell: opens every alert, the seen ones too, and takes the reader there.
 * A second press closes the seen ones again.
 */
function openAlerts() {
    setShowSeen(!healthState.showSeen);
    if (healthState.showSeen) {
        document.getElementById('anomalies-alert')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

/**
 * Open the report.
 *
 * It used to be a modal: the densest thing the app produces, opened the way a
 * confirmation dialog is, impossible to link to or leave up on a second
 * screen. Now it is a view, and this only navigates there - the fetch is
 * triggered by arriving, below.
 */
function showWeeklyReport() {
    navigate('reportes');
    fetchWeeklyReport();
}

/**
 * The report's state: one of the three is on screen, the others are hidden.
 *
 * The loading state is the skeleton, not a line of text, and an error keeps a
 * sentence on screen rather than an empty box.
 */
function showReportState(which, message = '') {
    document.getElementById('report-loading').hidden = which !== 'loading';
    document.getElementById('report-content').hidden = which !== 'content';
    const error = document.getElementById('report-error');
    error.hidden = which !== 'error';
    if (which === 'error') error.textContent = message;
}

/** "58 %" for a score, or "--" when there is none. */
function scoreText(score) {
    return Number.isFinite(score) ? `${Math.round(score)} %` : '--';
}

/** "14:00 · 58 %" for an insight, or the dash when the hour is unknown. */
function hourScoreText(hour, score) {
    if (!Number.isInteger(hour)) return '--';
    const when = `${String(hour).padStart(2, '0')}:00`;
    return Number.isFinite(score) ? `${when} · ${scoreText(score)}` : when;
}

function updateWeeklyReport(data) {
    if (data.error) {
        console.error('Weekly report error:', data.error);
        showReportState('error', 'No se pudo generar el reporte. Probá de nuevo.');
        return;
    }
    showReportState('content');

    // The heading says which week it is, in the words the grower reads.
    document.getElementById('report-title').textContent =
        weekLabel(data.report_period?.start, data.report_period?.end) || 'Reporte semanal';
    document.getElementById('report-samples').textContent =
        data.summary?.sample_count?.toLocaleString('es-AR') || '--';

    // The VPD score is the share of time in range: the same number the period
    // readout calls that, relabelled to say what it truthfully measures.
    const overall = data.vpd_score?.overall;
    const vpdEl = document.getElementById('report-vpd-score');
    vpdEl.innerHTML = `${Number.isFinite(overall) ? Math.round(overall) : '--'}`
        + '<span class="ac-readout__unit"> %</span>';
    // Coloured by the same thresholds as the hourly bars, so the number and the
    // bars cannot disagree about what is good.
    const tone = scoreTone(overall) === 'good' ? 'is-ok' : 'is-warn';
    vpdEl.className = `ac-readout__value ${Number.isFinite(overall) ? tone : 'is-empty'}`;
    updateTrend('report-vpd-trend', data.trends?.vpd_score, ' pts', true);

    const temp = data.summary?.temperature?.avg;
    document.getElementById('report-temp-avg').innerHTML =
        `${Number.isFinite(temp) ? fmtComma(temp, 1) : '--'}<span class="ac-readout__unit"> °C</span>`;
    updateTrend('report-temp-trend', data.trends?.temperature, ' °C');

    const humidity = data.summary?.humidity?.avg;
    document.getElementById('report-humidity-avg').innerHTML =
        `${Number.isFinite(humidity) ? fmtComma(humidity, 1) : '--'}<span class="ac-readout__unit"> %</span>`;
    updateTrend('report-humidity-trend', data.trends?.humidity, ' %');

    const insights = data.insights || {};
    document.getElementById('report-best').textContent =
        hourScoreText(insights.best_hour, insights.best_hour_score);
    document.getElementById('report-worst').textContent =
        hourScoreText(insights.worst_hour, insights.worst_hour_score);

    // Twenty-four columns, one per hour: an hour with no samples is an empty
    // column, not a missing one, so the hours stay where a person looks for them.
    const hours = hourlySlots(data.hourly_distribution);
    document.getElementById('report-hourly-chart').innerHTML = hours.map(({ hour, score }) => {
        const bar = hourBar(score);
        const when = `${String(hour).padStart(2, '0')}:00`;
        const text = bar.tone === 'empty' ? `${when} · sin muestras` : `${when} · ${scoreText(score)}`;
        return `<div class="rpt-hour is-${bar.tone}" style="height: ${bar.height}%"`
            + ` title="${escapeHtml(text)}"></div>`;
    }).join('');

    // Styled by tone class: the colour of a score is one rule in the stylesheet,
    // stated once, not a colour per row built here.
    const daily = data.vpd_score?.daily || [];
    document.getElementById('report-daily-scores').innerHTML = daily.map(day => {
        const bar = hourBar(day.score);
        const parsed = parseIsoDay(day.date);
        const label = parsed ? `${dayName(day.date)} ${parsed.day}` : day.date;
        return `
            <div class="rpt-day">
                <span class="rpt-day__name">${escapeHtml(label)}</span>
                <span class="rpt-day__track">
                    <span class="rpt-day__fill is-${bar.tone}" style="width: ${bar.height}%"></span>
                </span>
                <span class="rpt-day__value">${escapeHtml(scoreText(day.score))}</span>
            </div>`;
    }).join('');
}

/**
 * The change against last week, as a chip under the number.
 *
 * Only the VPD trend is coloured, and only because more time in range is good;
 * a temperature that moved is not good or bad by itself.
 */
function updateTrend(elementId, value, unit, invertColors = false) {
    const el = document.getElementById(elementId);
    const trend = trendText(value, unit, { invertColors });
    el.className = `rpt-trend is-${trend.tone}`;
    el.textContent = trend.text;
}

function fetchWeeklyReport() {
    // A report already on screen stays until its replacement lands; the
    // skeleton is for the first one only.
    if (document.getElementById('report-content').hidden) showReportState('loading');

    apiGet('/api/weekly-report', { key: 'weekly-report' }).then(data => {
        if (data) updateWeeklyReport(data);
        else showReportState('error', 'No se pudo generar el reporte. Probá de nuevo.');
    });
}

// A week of aggregates is the most expensive query the backend answers, so it
// runs when somebody actually opens the report rather than on a timer.
document.addEventListener('view:enter', event => {
    if (event.detail?.view === 'reportes') fetchWeeklyReport();
});

export {
    ackAlert, fetchAnomalies, fetchVpdScore, openAlerts, showWeeklyReport, snoozeAlert, toggleSeenAlerts,
};
