/**
 * The sidebar's collapsed state: wide for reading, icons only when the screen
 * needs the room back.
 *
 * The choice is remembered per browser, because the wall tablet and the
 * laptop want different things and both come back to the same page. Without
 * a remembered choice, a screen between 769 and 1100 px wide starts collapsed:
 * that is where the content is squeezed, and where a wide bar costs the most.
 */

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

/** Puts the sidebar in its starting state and wires its collapse button. */
export function wireSidebar() {
    const sidebar = document.querySelector('.ac-sidebar');
    const button = document.querySelector('[data-action="toggle-sidebar"]');
    if (!sidebar || !button) return;

    const narrow = window.matchMedia(NARROW_QUERY).matches;
    setCollapsed(sidebar, button, startsCollapsed(readStored(), narrow));

    button.addEventListener('click', () => {
        const collapsed = !sidebar.classList.contains('is-collapsed');
        setCollapsed(sidebar, button, collapsed);
        store(collapsed ? 'collapsed' : 'expanded');
    });
}
