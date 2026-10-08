/**
 * API client and connection state.
 *
 * Every request goes through apiGet so that timeouts, request de-duplication and
 * the online/offline indicator are handled in exactly one place.
 */

const connection = {
    consecutiveErrors: 0,
    lastSuccessAt: null,
    // Two misses in a row before complaining: one slow response on a busy
    // Raspberry is not an outage.
    get offline() { return this.consecutiveErrors >= 2; },
};

function renderConnectionState() {
    const dot = document.querySelector('.status-dot');
    const label = document.querySelector('.status-indicator span');
    const banner = document.getElementById('connection-alert');
    if (!dot || !label) return;

    if (connection.offline) {
        dot.classList.add('offline');
        label.textContent = 'Sin conexión al backend';
        if (banner) banner.classList.add('show');
    } else {
        dot.classList.remove('offline');
        label.textContent = 'Sistema Activo';
        if (banner) banner.classList.remove('show');
    }
}

const REQUEST_TIMEOUT_MS = 8000;
// One in-flight request per endpoint. Without this, a slow Raspberry makes
// the 3s pollers stack requests on top of each other.

const inFlight = new Map();

async function apiGet(url, { key = url } = {}) {
    if (inFlight.has(key)) return null;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    inFlight.set(key, controller);

    try {
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok && response.status >= 500) {
            throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        connection.consecutiveErrors = 0;
        connection.lastSuccessAt = Date.now();
        renderConnectionState();
        return data;
    } catch (error) {
        connection.consecutiveErrors += 1;
        renderConnectionState();
        console.error(`Error fetching ${url}:`, error);
        return null;
    } finally {
        clearTimeout(timer);
        inFlight.delete(key);
    }
}

export { connection, renderConnectionState, apiGet };
