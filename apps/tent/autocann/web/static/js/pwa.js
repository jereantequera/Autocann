/**
 * Registers the service worker that lets the dashboard open with the Raspberry
 * unreachable. The worker itself is autocann/web/sw_worker.js.
 *
 * Browsers offer service workers only on HTTPS or localhost. Over plain HTTP on
 * the LAN `navigator.serviceWorker` is missing, and this does nothing: the
 * dashboard still installs from its manifest, it just does not cache a shell.
 */

export function registerServiceWorker() {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
        .catch(error => console.warn('Service worker not registered:', error));
}
