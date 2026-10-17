
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
// The tent's identity: a monogram for the 40 px square, and words for it
// ---------------------------------------------------------------------------

/**
 * The two-character mark for the tent when the bar is 40 px wide.
 *
 * "Cultivo #1" becomes "C1": the first letter and the first run of digits,
 * because the number is what tells two grows apart on a square that small. A
 * name with no number ("Lechuga") gets its first two letters instead.
 */
export function monogram(name) {
    const text = String(name ?? '').trim();
    const letters = text.match(/\p{L}/gu) || [];
    const digits = text.match(/\d+/);
    if (letters.length === 0) return digits ? digits[0] : '';
    const second = digits ? digits[0] : (letters[1] || '');
    return (letters[0] + second).toLocaleUpperCase('es-AR');
}

/**
 * The tent block's words, the same ones the expanded block shows: where, which
 * grow, what stage, and the day. Collapsed, these are only ever read through
 * the title and the accessible name, so they have to say everything.
 */
export function tentLabel({ tent, name, stage, day, expected }) {
    const parts = [];
    if (tent) parts.push(tent);
    if (name) parts.push(name);
    if (stage) parts.push(stage);
    if (Number.isFinite(day)) {
        parts.push(expected ? `día ${day} de ~${expected}` : `día ${day}`);
    }
    return parts.join(' · ');
}

/** The tent's name, and the grow last painted, so either can change without the other. */
let tentName = '';
let activeGrow = {};

/** Writes the block's words from the tent's name and the grow that is active. */
function paintLabel() {
    const block = document.querySelector('.ac-sidebar__tent');
    if (!block) return;
    const label = tentLabel({ tent: tentName, ...activeGrow });
    block.title = label;
    block.setAttribute('aria-label', label);
}

/**
 * Shows the tent's name: on start-up the one the server rendered, and after a
 * rename the one just saved, so the sidebar is right without a reload.
 */
export function setTentName(name) {
    tentName = name;
    const text = document.getElementById('tent-name');
    if (text) text.textContent = name;
    paintLabel();
}

/** Writes the tent's monogram and its words from the grow that is active. */
export function paintTent(grow) {
    activeGrow = grow;
    const mark = document.getElementById('tent-mono');
    if (mark) mark.textContent = monogram(grow.name);
    paintLabel();
}

/** What the status button says on its own: the state, then the verb that changes it. */
export function stateLabel(text, action) {
    return [text, action].filter(Boolean).join(' · ');
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

/**
 * The status button's accessible name and, collapsed, its tooltip: the state
 * and the verb, because the words themselves are hidden in that mode.
 *
 * The title is only written while collapsed. Expanded, the chip keeps the
 * longer tooltip dashboard.js writes, which explains what stopping does.
 */
function syncStateLabel(sidebar, chip) {
    const label = stateLabel(
        document.getElementById('system-state-text')?.textContent.trim(),
        document.getElementById('system-state-action')?.textContent.trim(),
    );
    if (chip.getAttribute('aria-label') !== label) chip.setAttribute('aria-label', label);
    if (sidebar.classList.contains('is-collapsed') && chip.title !== label) chip.title = label;
}

function wireLoopState(sidebar) {
    const chip = document.getElementById('system-state');
    const text = document.getElementById('system-state-text');
    if (!chip || !text) return;

    // The equality checks above are what end the loops: writing the same words
    // again is not a change, so each observer hears it once and then goes quiet.
    // The chip is watched as a whole because dashboard.js writes its action and
    // its tooltip too, and a title that someone else rewrote has to be put back.
    new MutationObserver(() => {
        syncLoopState(chip, text);
        syncStateLabel(sidebar, chip);
    }).observe(chip, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ['title'] });
    document.addEventListener('health:updated', () => syncLoopState(chip, text));
    document.addEventListener('connection:changed', () => syncLoopState(chip, text));
    syncLoopState(chip, text);
    syncStateLabel(sidebar, chip);
}

/** Puts the sidebar in its starting state and wires its collapse button. */
export function wireSidebar() {
    const sidebar = document.querySelector('.ac-sidebar');
    const button = document.querySelector('[data-action="toggle-sidebar"]');
    if (!sidebar || !button) return;

    // The name the server rendered; setTentName() takes it from here on.
    tentName = document.getElementById('tent-name')?.textContent.trim() ?? '';
    paintLabel();

    const narrow = window.matchMedia(NARROW_QUERY).matches;
    setCollapsed(sidebar, button, startsCollapsed(readStored(), narrow));
    wireLoopState(sidebar);

    button.addEventListener('click', () => {
        const collapsed = !sidebar.classList.contains('is-collapsed');
        setCollapsed(sidebar, button, collapsed);
        const chip = document.getElementById('system-state');
        if (chip) syncStateLabel(sidebar, chip);
        store(collapsed ? 'collapsed' : 'expanded');
    });
}
