/*
 * Service worker for the dashboard: caches the static shell so the page opens
 * with the Raspberry unreachable. The API is never cached.
 *
 * Served at /sw.js by autocann/web/pwa.py, which puts `const SHELL = {version,
 * urls}` in front of this file. It is a classic script, not a module, because
 * a worker registered without { type: 'module' } cannot import one. The tests
 * in tests/js/pwa.test.mjs load it into a sandbox with a fake `self`.
 */

const CACHE_PREFIX = 'autocann-shell-';
const CACHE_NAME = CACHE_PREFIX + SHELL.version;

/** The JSON API: always the network. Its answers are the numbers on screen. */
function isApiRequest(url) {
    return new URL(url, self.location.href).pathname.startsWith('/api/');
}

/** The page and the static files, which the shell cache serves first. */
function isShellRequest(url) {
    const { pathname } = new URL(url, self.location.href);
    return pathname === '/' || pathname.startsWith('/static/');
}

async function precache() {
    const cache = await caches.open(CACHE_NAME);
    await Promise.all(SHELL.urls.map(async url => {
        // 'reload' skips the browser's HTTP cache, so the copy matches this deploy.
        const response = await fetch(url, { cache: 'reload' });
        if (response.ok) {
            await cache.put(url, response);
        } else if (url !== '/') {
            // A static file must be there, or the shell is broken and the old
            // worker keeps running. The page itself may answer 401 when the
            // PIN is on: then it is simply not stored, and the static files are.
            throw new Error(`${url} answered ${response.status}`);
        }
    }));
}

async function cacheFirst(request) {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(request, { ignoreSearch: true });
    return cached || fetch(request);
}

/** Pages go to the network first, so a deploy shows up; the cached page is the offline fallback. */
async function networkFirstPage(request) {
    try {
        return await fetch(request);
    } catch (error) {
        const cache = await caches.open(CACHE_NAME);
        const cached = await cache.match('/');
        if (cached) return cached;
        throw error;
    }
}

self.addEventListener('install', event => {
    event.waitUntil(precache().then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
    event.waitUntil((async () => {
        for (const name of await caches.keys()) {
            if (name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME) {
                await caches.delete(name);
            }
        }
        await self.clients.claim();
    })());
});

self.addEventListener('fetch', event => {
    const { request } = event;
    if (request.method !== 'GET') return;
    if (new URL(request.url).origin !== self.location.origin) return;
    // Not answered here: the browser sends it to the network as it would without a worker.
    if (isApiRequest(request.url)) return;

    if (request.mode === 'navigate') {
        event.respondWith(networkFirstPage(request));
    } else if (isShellRequest(request.url)) {
        event.respondWith(cacheFirst(request));
    }
});
