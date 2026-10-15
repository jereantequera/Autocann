/**
 * The view router.
 *
 * The dashboard used to be one column: live readings, a data-entry form, a
 * 30-day chart and the relay controls stacked in a single scroll. Each of those
 * is a different job, done at a different moment, by someone looking for a
 * different thing — so each gets its own view and only one is on screen.
 *
 * This module knows nothing about sensors, feeding or charts. It toggles which
 * section is visible and announces the change; whoever cares listens for
 * `view:enter`. That is what keeps the feature modules from having to import a
 * router, and the router from having to import them.
 */

/** Shown when the URL carries no view, or one that does not exist. */
const DEFAULT_VIEW = 'panel';

/** The view currently on screen; null until initViews() runs. */
let current = null;

/** The views actually in the page, read from the DOM so the markup decides. */
function viewIds() {
    return Array.from(document.querySelectorAll('.view'), section => section.dataset.view);
}

function viewFromHash() {
    const id = (window.location.hash || '').replace(/^#\/?/, '');
    return viewIds().includes(id) ? id : DEFAULT_VIEW;
}

function activate(id, { initial = false } = {}) {
    if (id === current) return;
    current = id;

    document.querySelectorAll('.view').forEach(section => {
        const on = section.dataset.view === id;
        section.classList.toggle('is-active', on);
        // Hidden from assistive tech too: three copies of "Temperatura" in the
        // accessibility tree is what a screen reader would otherwise read out.
        section.toggleAttribute('inert', !on);
    });

    document.querySelectorAll('.nav-tab').forEach(tab => {
        const on = tab.dataset.view === id;
        tab.classList.toggle('is-active', on);
        tab.setAttribute('aria-current', on ? 'page' : 'false');
    });

    // Landing halfway down the new view is disorienting; the previous view's
    // scroll position means nothing here.
    if (!initial) window.scrollTo({ top: 0 });

    document.dispatchEvent(new CustomEvent('view:enter', { detail: { view: id, initial } }));
}

/** Go to a view from code; the tabs themselves are plain links. */
function navigate(id) {
    const hash = `#/${id}`;
    if (window.location.hash === hash) activate(id);
    else window.location.hash = hash;
}

function currentView() {
    return current || viewFromHash();
}

/**
 * Put the right section on screen before anything is wired, so the loading
 * skeleton has somewhere to show.
 *
 * Classes only: no `view:enter`, and `current` stays unset, so initViews()
 * still runs the real activation - and its listeners - once their modules are
 * ready for it. Without this the page was blank for the time /api/config took.
 */
function showInitialView() {
    const id = viewFromHash();
    document.querySelectorAll('.view').forEach(section => {
        section.classList.toggle('is-active', section.dataset.view === id);
    });
    document.querySelectorAll('.nav-tab').forEach(tab => {
        tab.classList.toggle('is-active', tab.dataset.view === id);
    });
}

function initViews() {
    // The hash is the single source of truth, so the wall tablet comes back to
    // the same view after a reload and a view can be bookmarked.
    window.addEventListener('hashchange', () => activate(viewFromHash()));
    activate(viewFromHash(), { initial: true });
}

export { DEFAULT_VIEW, initViews, currentView, navigate, showInitialView };
