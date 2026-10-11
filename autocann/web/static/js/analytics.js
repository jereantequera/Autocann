import { apiGet } from './api.js';
import { showModal } from './modals.js';
import { escapeHtml, getScoreClass, getScoreLabel } from './util.js';

/** VPD score, anomaly banner and the weekly report modal. */

function updateVpdScore(data) {
    if (data.error) {
        console.error('VPD Score error:', data.error);
        return;
    }

    const scoreEl = document.getElementById('vpd-score-value');
    const labelEl = document.getElementById('vpd-score-label');
    const rangeEl = document.getElementById('vpd-score-range');
    const barEl = document.getElementById('vpd-score-bar');
    const labelsEl = document.getElementById('vpd-score-days-labels');

    const score = data.overall_score;
    if (score !== null && score !== undefined) {
        scoreEl.textContent = `${score}%`;
        scoreEl.className = `vpd-score-value ${getScoreClass(score)}`;
        labelEl.textContent = getScoreLabel(score);
    } else {
        scoreEl.textContent = '--%';
        labelEl.textContent = 'Sin datos suficientes';
    }

    if (data.vpd_range) {
        rangeEl.textContent = `${data.vpd_range.min}-${data.vpd_range.max} kPa`;
    }

    // Render daily bars
    if (data.daily_scores && data.daily_scores.length > 0) {
        const dayNames = ['Dom', 'Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb'];
        
        barEl.innerHTML = data.daily_scores.map(day => {
            const height = day.score != null ? day.score : 0;
            const dayDate = new Date(day.date + 'T12:00:00');
            const dayName = dayNames[dayDate.getDay()];
            return `
                <div class="vpd-score-day" title="${day.date}: ${day.score !== null ? day.score + '%' : 'Sin datos'}">
                    <div class="vpd-score-day-fill" style="height: ${height}%;"></div>
                </div>
            `;
        }).join('');

        labelsEl.innerHTML = data.daily_scores.map(day => {
            const dayDate = new Date(day.date + 'T12:00:00');
            const dayName = dayNames[dayDate.getDay()];
            return `<span>${dayName}</span>`;
        }).join('');
    }
}

function fetchVpdScore() {
    apiGet('/api/vpd-score?days=7').then(data => { if (data) updateVpdScore(data); });
}

const ANOMALIES_DISMISS_KEY = 'anomalies_dismissed_until';

const ANOMALIES_DISMISS_DURATION = 5 * 60 * 1000; // 5 minutes

function isAnomaliesDismissed() {
    try {
        const dismissedUntil = localStorage.getItem(ANOMALIES_DISMISS_KEY);
        if (!dismissedUntil) return false;
        const until = parseInt(dismissedUntil, 10);
        if (Number.isFinite(until) && Date.now() < until) return true;
        localStorage.removeItem(ANOMALIES_DISMISS_KEY);
        return false;
    } catch (e) {
        // Private browsing or blocked site data: just show the alerts.
        return false;
    }
}

function updateAnomalies(data) {
    if (data.error || isAnomaliesDismissed()) return;

    const alertEl = document.getElementById('anomalies-alert');
    const listEl = document.getElementById('anomalies-list');
    const iconEl = document.getElementById('anomalies-icon');
    const titleEl = document.getElementById('anomalies-title-text');

    const allIssues = [
        ...(data.anomalies || []).map(a => ({...a, severity: 'critical'})),
        ...(data.warnings || []).map(w => ({...w, severity: w.severity || 'warning'}))
    ];

    if (allIssues.length === 0) {
        alertEl.classList.remove('show', 'critical');
        return;
    }

    alertEl.classList.add('show');
    
    if (data.status === 'critical') {
        alertEl.classList.add('critical');
        iconEl.textContent = '🚨';
        titleEl.textContent = 'Problemas Críticos Detectados';
    } else {
        alertEl.classList.remove('critical');
        iconEl.textContent = '⚠️';
        titleEl.textContent = 'Advertencias Detectadas';
    }

    listEl.innerHTML = allIssues.slice(0, 5).map(issue => `
        <div class="anomaly-item ${issue.severity}">
            <span>${issue.severity === 'critical' ? '🔴' : '🟡'}</span>
            <span style="flex: 1;">${escapeHtml(issue.message)}</span>
            <span class="anomaly-time">${escapeHtml(issue.timestamp ? String(issue.timestamp).split(' ')[1] || '' : '')}</span>
        </div>
    `).join('');

    if (allIssues.length > 5) {
        listEl.innerHTML += `
            <div style="text-align: center; color: var(--text-muted); font-size: 0.85em; padding: 8px;">
                + ${allIssues.length - 5} más...
            </div>
        `;
    }
}

function dismissAnomalies() {
    // Save dismiss timestamp in localStorage (persists across page reloads)
    const dismissUntil = Date.now() + ANOMALIES_DISMISS_DURATION;
    try {
        localStorage.setItem(ANOMALIES_DISMISS_KEY, dismissUntil.toString());
    } catch (e) {
        // Not persistable here; hiding it for this page view is enough.
    }
    const alertEl = document.getElementById('anomalies-alert');
    alertEl.classList.remove('show');
}

function fetchAnomalies() {
    if (isAnomaliesDismissed()) return;
    
    apiGet('/api/anomalies?hours=24').then(data => { if (data) updateAnomalies(data); });
}

function showWeeklyReportModal() {
    showModal('weeklyReportModal');
    document.getElementById('report-loading').style.display = 'block';
    document.getElementById('report-content').style.display = 'none';
    fetchWeeklyReport();
}

function updateWeeklyReport(data) {
    if (data.error) {
        console.error('Weekly report error:', data.error);
        return;
    }

    document.getElementById('report-loading').style.display = 'none';
    document.getElementById('report-content').style.display = 'block';

    // Period
    if (data.report_period) {
        document.getElementById('report-period').textContent = 
            `${data.report_period.start} → ${data.report_period.end}`;
    }

    // VPD Score
    const vpdScore = data.vpd_score?.overall;
    document.getElementById('report-vpd-score').textContent = 
        vpdScore != null ? `${vpdScore}%` : '--%';
    
    // Trends
    updateTrend('report-vpd-trend', data.trends?.vpd_score, '%', true);
    updateTrend('report-temp-trend', data.trends?.temperature, '°C');
    updateTrend('report-humidity-trend', data.trends?.humidity, '%');

    // Summary stats
    document.getElementById('report-temp-avg').textContent = 
        data.summary?.temperature?.avg != null ? `${data.summary.temperature.avg}°C` : '--°C';
    document.getElementById('report-humidity-avg').textContent = 
        data.summary?.humidity?.avg != null ? `${data.summary.humidity.avg}%` : '--%';

    // Insights
    const insights = data.insights || {};
    document.getElementById('report-best-hour').textContent = 
        insights.best_hour != null ? `${String(insights.best_hour).padStart(2, '0')}:00` : '--:00';
    document.getElementById('report-best-score').textContent = 
        insights.best_hour_score != null ? `${insights.best_hour_score}% score` : '-- score';
    document.getElementById('report-worst-hour').textContent = 
        insights.worst_hour != null ? `${String(insights.worst_hour).padStart(2, '0')}:00` : '--:00';
    document.getElementById('report-worst-score').textContent = 
        insights.worst_hour_score != null ? `${insights.worst_hour_score}% score` : '-- score';

    // Hourly chart
    const hourlyData = data.hourly_distribution || [];
    const chartEl = document.getElementById('report-hourly-chart');
    const labelsEl = document.getElementById('report-hourly-labels');
    
    if (hourlyData.length > 0) {
        chartEl.innerHTML = hourlyData.map(h => {
            const height = h.vpd_score || 0;
            return `<div class="hourly-bar" style="height: ${height}%;" title="${h.hour_label}: ${h.vpd_score}%"></div>`;
        }).join('');

        labelsEl.innerHTML = hourlyData.map((h, i) => {
            // Only show every 3rd label to avoid crowding
            return `<span>${i % 3 === 0 ? h.hour : ''}</span>`;
        }).join('');
    }

    // Daily scores
    const dailyScores = data.vpd_score?.daily || [];
    const dailyEl = document.getElementById('report-daily-scores');
    const dayNames = ['Domingo', 'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado'];
    
    dailyEl.innerHTML = dailyScores.map(day => {
        const dayDate = new Date(day.date + 'T12:00:00');
        const dayName = dayNames[dayDate.getDay()];
        const score = day.score;
        const scoreClass = getScoreClass(score);
        const barWidth = score != null ? score : 0;
        
        return `
            <div style="display: flex; align-items: center; gap: 12px;">
                <span style="width: 80px; font-size: 0.85em; color: var(--text-secondary);">${dayName}</span>
                <div style="flex: 1; height: 24px; background: var(--bg-secondary); border-radius: 4px; overflow: hidden;">
                    <div style="height: 100%; width: ${barWidth}%; background: linear-gradient(90deg, #a855f7, #c084fc); transition: width 0.5s;"></div>
                </div>
                <span style="width: 50px; text-align: right; font-family: 'JetBrains Mono', monospace; font-weight: 600; color: ${score == null ? 'var(--text-muted)' : score >= 70 ? 'var(--accent-green)' : score >= 50 ? 'var(--vpd-adjusting)' : 'var(--vpd-danger)'};">
                    ${score != null ? score + '%' : '--'}
                </span>
            </div>
        `;
    }).join('');

    // Sample count
    document.getElementById('report-samples').textContent = 
        data.summary?.sample_count?.toLocaleString('es-AR') || '--';
}

function updateTrend(elementId, value, unit, invertColors = false) {
    const el = document.getElementById(elementId);
    if (value === null || value === undefined) {
        el.className = 'report-trend neutral';
        el.textContent = '= sin cambio';
        return;
    }

    const isUp = value > 0;
    const isDown = value < 0;
    
    if (invertColors) {
        // For VPD score, up is good (green), down is bad (red)
        el.className = `report-trend ${isUp ? 'down' : isDown ? 'up' : 'neutral'}`;
    } else {
        el.className = `report-trend ${isUp ? 'up' : isDown ? 'down' : 'neutral'}`;
    }
    
    const arrow = isUp ? '↑' : isDown ? '↓' : '=';
    el.textContent = `${arrow} ${Math.abs(value)}${unit} vs semana ant.`;
}

function fetchWeeklyReport() {
    apiGet('/api/weekly-report').then(data => {
        if (data) {
            updateWeeklyReport(data);
        } else {
            document.getElementById('report-loading').textContent = 'Error al cargar reporte';
        }
    });
}

// Time period selector - sync both selectors

export { fetchVpdScore, fetchAnomalies, dismissAnomalies, showWeeklyReportModal };
