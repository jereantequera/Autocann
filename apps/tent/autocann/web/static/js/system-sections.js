/**
 * The rest of Sistema: the database counters, the PIN session, the in-page
 * index and the theme choice.
 */

import { apiGet, apiPost } from './api.js';

// ---------------------------------------------------------------------------
// Database
// ---------------------------------------------------------------------------

function renderDatabase(stats) {
    const set = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    };
    const count = value => (Number.isFinite(value) ? value.toLocaleString('es-AR') : '--');

    set('stat-db-size', stats.database_size_mb !== undefined ? `${stats.database_size_mb} MB` : '--');
    set('stat-grows', count(stats.grow_count));
    set('stat-readings', count(stats.sensor_data_count));
    set('stat-events', count(stats.control_events_count));
    set('stat-span', stats.oldest_record
        ? `${stats.oldest_record.split(' ')[0]} → ${(stats.newest_record || '').split(' ')[0]}`
        : 'sin lecturas');
}

export function fetchDatabase() {
    // Not polled: the file grows by kilobytes an hour, and nobody watches this
    // card waiting for it to move.
    apiGet('/api/database-stats', { key: 'database-stats' })
        .then(data => { if (data) renderDatabase(data); });
}

// ---------------------------------------------------------------------------
// Session
// ---------------------------------------------------------------------------
// Only shown where there is a PIN to log out of. `/api/auth` answers whether
// one is configured even when it is not, so the dashboard reads the
// configuration instead of inferring it from a 401 it would never see while
// logged in.

export function fetchSession() {
    apiGet('/api/auth', { key: 'auth' }).then(data => {
        const box = document.getElementById('session-section');
        if (box && data) box.hidden = !data.pin_required;
    });
}

export async function logout() {
    const result = await apiPost('/api/logout', {});
    // Reload either way: on success the next request answers 401 and lands on
    // the login page, and on failure the page should show whatever is actually
    // true rather than a button that appeared to do nothing.
    window.location.reload();
    return result;
}

// ---------------------------------------------------------------------------
// The screen: index, theme
// ---------------------------------------------------------------------------

/** The in-page index: scrolls to the section and marks the link, without touching the hash. */
export function jumpTo(sectionId, view) {
    const target = document.getElementById(sectionId);
    if (!target) return;
    target.scrollIntoView({
        behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        block: 'start',
    });
    view.querySelectorAll('[data-sys-jump]').forEach(link => {
        const current = link.dataset.sysJump === sectionId;
        link.classList.toggle('is-current', current);
        if (current) link.setAttribute('aria-current', 'location');
        else link.removeAttribute('aria-current');
    });
}

export function paintThemeChoice(name) {
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
        button.setAttribute('aria-pressed', String(button.dataset.themeChoice === name));
    });
}
