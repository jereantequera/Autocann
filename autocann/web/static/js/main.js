import { apiGet } from './api.js';
import { dismissAnomalies, fetchAnomalies, fetchVpdScore, showWeeklyReportModal } from './analytics.js';
import { initCharts } from './charts.js';
import {
    fetchCurrentData, fetchDatabaseStats, fetchHistoricalData, fetchOutputsStatus,
    fetchPeriodSummary, fetchSensorStatus, updateSummaryPeriodLabel,
} from './dashboard.js';
import {
    activateGrow, createGrow, endGrow, fetchActiveGrow, showGrowModal, showNewGrowModal,
    showStageModal, updateStage, wireGrowListActions,
} from './grows.js';
import { closeModal, closeTopModal } from './modals.js';
import { applyConfig, state } from './state.js';

/**
 * Entry point: configuration, event wiring and the polling schedule.
 */

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------
// A hidden tab used to keep polling every 3 seconds forever, which drains a
// phone battery and keeps the Raspberry busy for nobody's benefit.

const POLLERS = [
    { fn: fetchCurrentData, every: 3000 },
    { fn: fetchOutputsStatus, every: 3000 },
    { fn: fetchSensorStatus, every: 30000 },
    { fn: () => { fetchHistoricalData(state.period, state.interval, state.hours); fetchPeriodSummary(); },
      every: 30000 },
    { fn: fetchDatabaseStats, every: 60000 },
    { fn: fetchActiveGrow, every: 60000 },
    { fn: fetchVpdScore, every: 5 * 60 * 1000 },
    { fn: fetchAnomalies, every: 60000 },
];

let pollTimers = [];

function startPolling() {
    stopPolling();
    pollTimers = POLLERS.map(p => setInterval(() => {
        if (document.hidden) return;
        p.fn();
    }, p.every));
}

function stopPolling() {
    pollTimers.forEach(clearInterval);
    pollTimers = [];
}

function refreshAll() {
    fetchCurrentData();
    fetchOutputsStatus();
    fetchSensorStatus();
    fetchDatabaseStats();
    fetchActiveGrow();
    fetchHistoricalData(state.period, state.interval, state.hours);
    fetchPeriodSummary();
    fetchVpdScore();
    fetchAnomalies();
}

// ---------------------------------------------------------------------------
// Time range selector
// ---------------------------------------------------------------------------

function handleTimeBtnClick(clickedBtn) {
    document.querySelectorAll('.time-btn').forEach(b => b.classList.remove('active'));

    const hours = clickedBtn.dataset.hours;
    const period = clickedBtn.dataset.period;
    const interval = clickedBtn.dataset.interval;

    // The page has two selectors; keep them in sync.
    document.querySelectorAll('.time-btn').forEach(btn => {
        if (hours && btn.dataset.hours === hours) btn.classList.add('active');
        else if (period && btn.dataset.period === period) btn.classList.add('active');
    });

    if (hours) {
        state.hours = parseFloat(hours);
        state.period = null;
    } else {
        state.hours = null;
        state.period = parseInt(period, 10);
    }
    state.interval = interval;

    fetchHistoricalData(state.period, state.interval, state.hours);
    updateSummaryPeriodLabel();
    fetchPeriodSummary();
}

// ---------------------------------------------------------------------------
// Event wiring
// ---------------------------------------------------------------------------
// Every handler is attached here rather than through onclick attributes. ES
// modules are scoped, so inline onclick could not reach these functions anyway —
// and building handler strings out of database values is what produced the XSS
// in the grow list.

const ACTIONS = {
    'weekly-report': showWeeklyReportModal,
    'show-grows': showGrowModal,
    'show-stage': showStageModal,
    'new-grow': showNewGrowModal,
    'create-grow': createGrow,
    'update-stage': updateStage,
    'dismiss-anomalies': dismissAnomalies,
};

function wireActions() {
    document.addEventListener('click', event => {
        const trigger = event.target.closest('[data-action]');
        if (!trigger) return;

        const action = trigger.dataset.action;
        if (ACTIONS[action]) {
            event.preventDefault();
            ACTIONS[action]();
            return;
        }
        if (action === 'close-modal') {
            event.preventDefault();
            closeModal(trigger.dataset.modal);
        }
    });

    // Click on the backdrop closes the modal.
    window.addEventListener('click', event => {
        if (event.target.classList.contains('modal')) {
            event.target.classList.remove('show');
        }
    });

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') closeTopModal();
    });

    document.querySelectorAll('.time-btn').forEach(btn => {
        btn.addEventListener('click', () => handleTimeBtnClick(btn));
    });

    document.addEventListener('visibilitychange', () => {
        // Coming back to the tab: refresh now instead of waiting out the
        // interval with stale numbers on screen.
        if (!document.hidden) refreshAll();
    });
}

// ---------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------

async function start() {
    // Stage ranges and names come from the server so they are not a second copy
    // of what lives in vpd_math.py.
    applyConfig(await apiGet('/api/config'));

    initCharts();
    wireActions();
    wireGrowListActions();
    updateSummaryPeriodLabel();
    refreshAll();
    startPolling();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
} else {
    start();
}

// The grow list renders its own buttons and needs these; everything else is
// reached through data-action.
export { activateGrow, endGrow, refreshAll, startPolling, stopPolling };
