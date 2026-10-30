import { apiGet, whenSettled } from './api.js';
import { dismissAnomalies, fetchAnomalies, fetchVpdScore, showWeeklyReport } from './analytics.js';
import { wireCalendar } from './calendar.js';
import { initCharts, resizeCharts } from './charts.js';
import {
    enterMonitorMode, enterTestMode,
    fetchCurrentData, fetchHistoricalData, fetchOutputsStatus,
    fetchPeriodSummary, setTestOutput, toggleTestMode, updateSummaryPeriodLabel,
} from './dashboard.js';
import { fetchHealth } from './health.js';
import {
    addPlanProduct, fetchFeedPlan, fetchFeeding, resetFormToPlan, saveIrrigation,
    savePlan, togglePlanEditor, wireFeeding,
} from './feeding.js';
import {
    activateGrow, createGrow, endGrow, fetchActiveGrow,
    showStageModal, updateStage, wireGrowFilter, wireGrowListActions,
} from './grows.js';
import { openNewGrowForm, wireNewGrowForm } from './growform.js';
import { wireTentName } from './tent-name.js';
import { closeModal, initModals } from './modals.js';
import { fetchLogbook } from './logbook.js';
import { fetchPanelHistory, wirePanel } from './panel.js';
import { wireQuiet } from './quiet.js';
import { wireRemote } from './remote.js';
import { wireSidebar } from './sidebar.js';
import { applyTheme, readTheme } from './theme.js';
import { applyConfig, state } from './state.js';
import { fetchNodeLog, fetchNodes, wireNodes } from './nodes.js';
import { wireSystem } from './system.js';
import { currentView, initViews, showInitialView } from './views.js';

/**
 * Entry point: configuration, event wiring and the polling schedule.
 */

// The theme is applied before anything is drawn: the tokens swap on the root
// element, so doing it here, at module evaluation, is the earliest a file of
// ours can run (index.html has no inline script for it, by design).
applyTheme(readTheme());

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------
// A hidden tab used to keep polling every 3 seconds forever, which drains a
// phone battery and keeps the Raspberry busy for nobody's benefit.
//
// `views` names where a poller's numbers are actually on screen; one without it
// runs everywhere, because what it feeds - the grow bar, the alert strip - is on
// every view. Polling the 30-day aggregate while somebody reads the calendar is
// the same waste as polling a hidden tab, and the Raspberry is the one paying.

const POLLERS = [
    { fn: fetchCurrentData, every: 3000, views: ['panel'] },
    { fn: fetchOutputsStatus, every: 3000, views: ['panel'] },
    // One call for the whole verdict - sensores, lazo e histórico - en vez de
    // dos que podían contestar cosas distintas. The node posts each sensor
    // every 10 s, so polling faster buys nothing but load on the Raspberry.
    // Sin `views` a propósito: esta respuesta alimenta el estado de la barra
    // lateral ("Lazo caído") y el cartel de arriba, que se ven en todas las
    // vistas. Si sólo corriera en Panel y Sistema, el cartel de "roto" tardaría
    // en aparecer o quedaría viejo en Cultivo y Riego, justo donde se está
    // mirando otra cosa.
    { fn: fetchHealth, every: 10000 },
    { fn: fetchPanelHistory, every: 60000, views: ['panel'] },
    // The bitácora is a week of entries that change a few times a day: a
    // minute is plenty, and the tab also fetches when it is opened.
    { fn: fetchLogbook, every: 60000, views: ['panel'] },
    { fn: () => { fetchHistoricalData(state.period, state.interval, state.hours); fetchPeriodSummary(); },
      every: 30000, views: ['stats'] },
    { fn: fetchVpdScore, every: 5 * 60 * 1000, views: ['stats'] },
    // Watering is a once-a-day act; this only has to notice a stage change or
    // a log entry made from another device. The panel shows how long it has
    // been since the last one, so it is needed there too.
    { fn: fetchFeeding, every: 60000, views: ['panel', 'riego'] },
    { fn: fetchActiveGrow, every: 60000 },
    { fn: fetchAnomalies, every: 60000 },
    // El log del nodo se mira mientras pasa algo: alguien abre Sistema
    // justamente cuando está tocando el nodo, y un log que no avanza hasta que
    // recargues la página no sirve para eso. El nodo reporta cada 30 s.
    { fn: fetchNodeLog, every: 15000, views: ['sistema'] },
    { fn: fetchNodes, every: 30000, views: ['sistema'] },
];

let pollTimers = [];

/** Whether this poller feeds anything the view on screen can show. */
function isPollerActive(poller) {
    return !poller.views || poller.views.includes(currentView());
}

function startPolling() {
    stopPolling();
    pollTimers = POLLERS.map(p => setInterval(() => {
        if (document.hidden) return;
        if (!isPollerActive(p)) return;
        p.fn();
    }, p.every));
}

function stopPolling() {
    pollTimers.forEach(clearInterval);
    pollTimers = [];
}

/**
 * Fetch everything the current view needs, now.
 *
 * Driven off POLLERS rather than its own list of calls: the two used to be
 * maintained side by side, which is how a poller gets added and the refresh on
 * tab focus keeps showing a stale number for a whole interval.
 */
function refreshAll() {
    POLLERS.forEach(p => { if (isPollerActive(p)) p.fn(); });
}

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------
// One loading state, designed, instead of whatever each placeholder happened to
// say: "Cargando...", "--", an hourglass, a chart with made-up axes, and then
// the alerts landing last and pushing the whole panel down 110 px. Until the
// first answers are in, the page shows skeletons (app.css, LOADING); then it
// is revealed once, saying everything it is going to say.

/** Views whose first round of data has arrived; a later visit shows the last numbers. */
const loadedViews = new Set();

async function revealWhenSettled(target, className, view) {
    await whenSettled();
    target.classList.remove(className);
    loadedViews.add(view);
}

/** The first visit to a view gets the same skeletons the first page load does. */
function loadView(view) {
    if (loadedViews.has(view)) return;
    const section = document.querySelector(`.view[data-view="${view}"]`);
    if (!section) return;
    section.classList.add('is-loading');
    revealWhenSettled(section, 'is-loading', view);
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
    'weekly-report': showWeeklyReport,
    'show-stage': showStageModal,
    'new-grow': openNewGrowForm,
    'create-grow': createGrow,
    'update-stage': updateStage,
    'dismiss-anomalies': dismissAnomalies,
    'toggle-feed-plan': togglePlanEditor,
    'feed-plan-save': savePlan,
    'feed-plan-add': addPlanProduct,
    'feed-save': saveIrrigation,
    'feed-reset': resetFormToPlan,
    // One control, in the header, so which way it goes depends on the state.
    // There used to be a second one - the banner's own Reanudar button - which
    // went away with the banner: a state with two ways out is two things to
    // keep in step for no gain.
    //
    // Stopping forks, so the chip opens the dialog that asks which way; these
    // two are its buttons, and they are the only way into either state.
    'toggle-pause': toggleTestMode,
    'enter-monitor': enterMonitorMode,
    'enter-pause': enterTestMode,
    'test-output': setTestOutput,
};

function wireActions() {
    document.addEventListener('click', event => {
        const trigger = event.target.closest('[data-action]');
        if (!trigger) return;

        const action = trigger.dataset.action;
        if (ACTIONS[action]) {
            event.preventDefault();
            // The element is passed so a handler can read its data-* attributes:
            // which output a relay button belongs to lives there, not in a
            // closure rebuilt on every render.
            ACTIONS[action](trigger);
            return;
        }
        if (action === 'close-modal') {
            event.preventDefault();
            closeModal(trigger.dataset.modal);
        }
    });

    // Escape, the backdrop and the focus trap all live in modals.js now: they
    // are three halves of one behaviour, and keeping them here is how closing
    // by backdrop ended up skipping the part that puts focus back.
    initModals();

    document.querySelectorAll('.time-btn').forEach(btn => {
        btn.addEventListener('click', () => handleTimeBtnClick(btn));
    });

    document.addEventListener('visibilitychange', () => {
        // Coming back to the tab: refresh now instead of waiting out the
        // interval with stale numbers on screen.
        if (!document.hidden) refreshAll();
    });

    document.addEventListener('view:enter', event => {
        // The first one fires during startup, where refreshAll() below already
        // runs; refreshing twice would just be two requests the de-duplication
        // has to throw away.
        if (event.detail?.initial) return;
        loadView(event.detail.view);
        refreshAll();
        // A canvas built inside a hidden section has no size until something
        // asks it to measure itself again.
        resizeCharts();
    });
}

// ---------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------

async function start() {
    // First, so the bar is in its starting state before the skeleton comes off.
    wireSidebar();
    showInitialView();
    // Stage ranges and names come from the server so they are not a second copy
    // of what lives in vpd_math.py.
    applyConfig(await apiGet('/api/config'));

    initCharts();
    wireActions();
    wireGrowListActions();
    wireGrowFilter();
    wireFeeding();
    wireCalendar();
    wirePanel();
    wireSystem();
    wireTentName();
    wireNewGrowForm();
    wireQuiet();
    wireRemote();
    wireNodes();
    updateSummaryPeriodLabel();
    // The chart has to be in hand before the feeding panel can render a row
    // per product, so it is fetched once here rather than on the poll.
    await fetchFeedPlan();
    // Last of the wiring: it decides which view is on screen, and the pollers
    // below ask that question to know what is worth fetching.
    initViews();
    refreshAll();
    resizeCharts();
    startPolling();
    await revealWhenSettled(document.body, 'is-booting', currentView());
    // Measured while hidden behind the skeleton, so measure again now.
    resizeCharts();
}

/**
 * Startup, and the skeleton comes off whatever happens to it: a startup that
 * throws halfway must not leave a page of grey bars that looks like it is
 * still loading forever.
 */
function boot() {
    start().catch(error => {
        console.error('Startup failed:', error);
        document.body.classList.remove('is-booting');
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
} else {
    boot();
}

// The grow list renders its own buttons and needs these; everything else is
// reached through data-action.
export { activateGrow, endGrow, isPollerActive, refreshAll, startPolling, stopPolling };
