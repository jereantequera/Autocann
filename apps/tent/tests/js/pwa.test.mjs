/**
 * Tests for the service worker (autocann/web/sw_worker.js).
 *
 * The worker is a classic script that registers event listeners on `self`, so
 * it is run here in a vm sandbox with a fake `self`, `caches` and `fetch`. What
 * is checked: which requests it answers and from where, what the install
 * precaches, and what the activate step throws away.
 *
 * Run with: node --test tests/js/*.test.mjs
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const WORKER = new URL('../../autocann/web/sw_worker.js', import.meta.url);
const ORIGIN = 'http://tent.local:8000';
const VERSION = 'v1-abc';

/** A worker in a fresh sandbox, with whatever the test needs to observe. */
function loadWorker({ urls = ['/', '/static/js/main.js', '/static/css/app.css'], fetchImpl, cacheEntries = {} } = {}) {
    const listeners = {};
    const fetched = [];
    const stores = {};
    const cacheNamed = name => {
        if (!stores[name]) stores[name] = new Map();
        return stores[name];
    };
    const self = {
        location: { href: `${ORIGIN}/sw.js`, origin: ORIGIN },
        addEventListener: (type, fn) => { listeners[type] = fn; },
        skipWaiting: async () => {},
        clients: { claim: async () => {} },
    };
    const caches = {
        open: async name => {
            const store = cacheNamed(name);
            return {
                put: async (key, response) => { store.set(new URL(key, ORIGIN).pathname, response); },
                // Entries are keyed by pathname, so this behaves as ignoreSearch: true,
                // which is what the worker asks for.
                match: async (request, options = {}) => {
                    assert.equal(options.ignoreSearch === true || request === '/', true);
                    const key = new URL(typeof request === 'string' ? request : request.url, ORIGIN).pathname;
                    return store.get(key);
                },
            };
        },
        keys: async () => Object.keys(stores),
        delete: async name => { delete stores[name]; return true; },
    };
    const fetch = async (url, options) => {
        fetched.push(String(url));
        return fetchImpl ? fetchImpl(String(url), options) : { ok: true, status: 200, url: String(url) };
    };
    for (const [name, entries] of Object.entries(cacheEntries)) {
        const store = cacheNamed(name);
        for (const [key, value] of Object.entries(entries)) store.set(key, value);
    }
    const context = {
        self, caches, fetch, console, URL,
        SHELL: { version: VERSION, urls },
    };
    vm.runInNewContext(readFileSync(WORKER, 'utf8'), context);
    return { listeners, fetched, stores, context };
}

/** Feeds a fetch event to the worker; returns the promise it handed to respondWith, if any. */
function fetchEvent(listeners, url, mode = 'cors', method = 'GET') {
    let answer;
    listeners.fetch({
        request: { url: `${ORIGIN}${url}`, method, mode },
        respondWith: promise => { answer = promise; },
    });
    return answer;
}

test('API requests are not answered by the worker, so they always reach the network', () => {
    const { context, listeners } = loadWorker();
    assert.equal(context.isApiRequest(`${ORIGIN}/api/current-data`), true);
    assert.equal(context.isApiRequest(`${ORIGIN}/api/outputs/desired?x=1`), true);
    assert.equal(fetchEvent(listeners, '/api/current-data'), undefined);
    assert.equal(fetchEvent(listeners, '/api/outputs/desired', 'cors'), undefined);
});

test('the shell is the page and everything under /static/; nothing else', () => {
    const { context } = loadWorker();
    assert.equal(context.isShellRequest(`${ORIGIN}/`), true);
    assert.equal(context.isShellRequest(`${ORIGIN}/static/css/app.css?v=1`), true);
    assert.equal(context.isShellRequest(`${ORIGIN}/api/config`), false);
    assert.equal(context.isShellRequest(`${ORIGIN}/sw.js`), false);
    assert.equal(context.isApiRequest(`${ORIGIN}/apiary`), false);
});

test('a shell file is served from the cache even when the backend is down', async () => {
    const cached = { ok: true, status: 200, cached: true };
    const { listeners, fetched } = loadWorker({
        cacheEntries: { [`autocann-shell-${VERSION}`]: { '/static/js/main.js': cached } },
        fetchImpl: async () => { throw new TypeError('Failed to fetch'); },
    });
    // The module import has no ?v=, the page's own link does: both must hit the cache.
    const answer = await fetchEvent(listeners, '/static/js/main.js');
    assert.equal(answer, cached);
    const withQuery = await fetchEvent(listeners, '/static/js/main.js?v=1');
    assert.equal(withQuery, cached);
    assert.deepEqual(fetched, []);
});

test('a navigation goes to the network first and falls back to the cached page offline', async () => {
    const page = { ok: true, status: 200, page: true };
    const offline = loadWorker({
        cacheEntries: { [`autocann-shell-${VERSION}`]: { '/': page } },
        fetchImpl: async () => { throw new TypeError('Failed to fetch'); },
    });
    assert.equal(await fetchEvent(offline.listeners, '/#riego', 'navigate'), page);

    const online = loadWorker({
        cacheEntries: { [`autocann-shell-${VERSION}`]: { '/': page } },
        fetchImpl: async () => ({ ok: true, status: 200, fresh: true }),
    });
    assert.equal((await fetchEvent(online.listeners, '/', 'navigate')).fresh, true);
});

test('requests to another origin and non-GET requests are left alone', () => {
    const { listeners } = loadWorker();
    listeners.fetch({
        request: { url: 'https://cdn.example.com/x.js', method: 'GET', mode: 'cors' },
        respondWith: () => assert.fail('cross-origin request was answered'),
    });
    assert.equal(fetchEvent(listeners, '/static/js/main.js', 'cors', 'POST'), undefined);
});

test('install precaches every shell file, and tolerates the page answering 401 under a PIN', async () => {
    const { listeners, stores } = loadWorker({
        urls: ['/', '/static/js/main.js'],
        fetchImpl: async url => (url === '/' ? { ok: false, status: 401 } : { ok: true, status: 200 }),
    });
    let pending;
    listeners.install({ waitUntil: promise => { pending = promise; } });
    await pending;
    const store = stores[`autocann-shell-${VERSION}`];
    assert.ok(store.has('/static/js/main.js'));
    assert.equal(store.has('/'), false);
});

test('install fails when a static file cannot be fetched, so the old worker keeps the shell', async () => {
    const { listeners } = loadWorker({
        urls: ['/static/js/main.js'],
        fetchImpl: async () => ({ ok: false, status: 500 }),
    });
    let pending;
    listeners.install({ waitUntil: promise => { pending = promise; } });
    await assert.rejects(pending, /500/);
});

test('activate deletes the old shell caches and keeps the current one and unrelated caches', async () => {
    const { listeners, stores } = loadWorker({
        cacheEntries: {
            'autocann-shell-v0-old': {},
            [`autocann-shell-${VERSION}`]: {},
            'some-other-cache': {},
        },
    });
    let pending;
    listeners.activate({ waitUntil: promise => { pending = promise; } });
    await pending;
    assert.deepEqual(Object.keys(stores).sort(), [`autocann-shell-${VERSION}`, 'some-other-cache']);
});
