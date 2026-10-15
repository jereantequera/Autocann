
/**
 * The sidebar's collapsed state: wide for reading, icons only when the screen
 * needs the room back.
 *
 * The choice is remembered per browser, because the wall tablet and the
 * laptop want different things and both come back to the same page. Without
 * a remembered choice, a screen between 769 and 1100 px wide starts collapsed:
 * that is where the content is squeezed, and where a wide bar costs the most.
 */

import { latestHealth } from './health.js';

const STORAGE_KEY = 'autocann.sidebar';
const NARROW_QUERY = '(min-width: 769px) and (max-width: 1100px)';

/** Whether the bar starts collapsed, given what was stored (or null) and whether the screen is narrow. */
export function startsCollapsed(stored, narrow) {
    if (stored === 'collapsed') return true;
    if (stored === 'expanded') return false;
    return narrow;
}

function readStored() {
    // Private windows and blocked site data throw here; the bar then just
    // starts from the screen width, which is the same answer as a first visit.
    try {
        return localStorage.getItem(STORAGE_KEY);
    } catch {
        return null;
    }
}

function store(value) {
    try {
        localStorage.setItem(STORAGE_KEY, value);
    } catch {
        // The choice lasts until the page is closed, which is no worse than
        // the default. Not worth a message.
    }
}

function setCollapsed(sidebar, button, collapsed) {
    sidebar.classList.toggle('is-collapsed', collapsed);
    // Said twice on purpose: the label is what a screen reader announces, and
    // aria-expanded is what it reports as the state of the control.
    button.setAttribute('aria-expanded', String(!collapsed));
    button.setAttribute('aria-label', collapsed ? 'Expandir la barra' : 'Colapsar la barra');
}


// ---------------------------------------------------------------------------
// The loop's state in the status block
// ---------------------------------------------------------------------------
// The chip is written by dashboard.js, which has no idea whether the control
// loop is alive: it says "Sistema activo" whenever nothing is paused. So when
// /api/health says the loop is down, this overrides the words, and it has to
// watch the chip, because dashboard.js writes it again on its own schedule.

const LOOP_DOWN_TEXT = 'Lazo caído';

/** Whether the loop, by /api/health's own account, is not running. */
export function loopIsDown(health) {
    return !!health && !health.error && health.loop?.state === 'down';
}

/**
 * The words the status block should say about the loop, or null to leave the
 * chip as dashboard.js wrote it.
 *
 * Paused, monitoring and an unreachable backend each already say something
 * true and intentional, so none of them is overridden: "Lazo caído" is only
 * for a loop that should be running and is not.
 */
export function loopOverride({ health, paused = false, monitoring = false, offline = false }) {
    if (paused || monitoring || offline) return null;
    return loopIsDown(health) ? LOOP_DOWN_TEXT : null;
}

function syncLoopState(chip, text) {
    const override = loopOverride({
        health: latestHealth(),
        paused: chip.classList.contains('is-paused'),
        monitoring: chip.classList.contains('is-monitor'),
        offline: chip.classList.contains('is-offline'),
    });
    chip.classList.toggle('is-loop-down', override !== null);
    if (override !== null) {
        if (text.textContent !== override) text.textContent = override;
    } else if (text.textContent === LOOP_DOWN_TEXT) {
        // Only ever undoes its own words: whatever dashboard.js says next wins
        // from here, and "Sistema activo" is the state this override replaced.
        text.textContent = 'Sistema activo';
    }
}

function wireLoopState() {
    const chip = document.getElementById('system-state');
    const text = document.getElementById('system-state-text');
    if (!chip || !text) return;

    // The equality check above is what ends the loop: writing the same words
    // again is not a change, so the observer hears it once and then goes quiet.
    new MutationObserver(() => syncLoopState(chip, text))
        .observe(text, { childList: true, characterData: true, subtree: true });
    document.addEventListener('health:updated', () => syncLoopState(chip, text));
    document.addEventListener('connection:changed', () => syncLoopState(chip, text));
    syncLoopState(chip, text);
}

/** Puts the sidebar in its starting state and wires its collapse button. */
export function wireSidebar() {
    const sidebar = document.querySelector('.ac-sidebar');
    const button = document.querySelector('[data-action="toggle-sidebar"]');
    if (!sidebar || !button) return;

    const narrow = window.matchMedia(NARROW_QUERY).matches;
    setCollapsed(sidebar, button, startsCollapsed(readStored(), narrow));
    wireLoopState();

    button.addEventListener('click', () => {
        const collapsed = !sidebar.classList.contains('is-collapsed');
        setCollapsed(sidebar, button, collapsed);
        store(collapsed ? 'collapsed' : 'expanded');
    });
}
