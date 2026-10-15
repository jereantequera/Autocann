/**
 * El tema: lo que se guarda, lo que se pone en <html>, y lo que pasa cuando
 * el navegador no deja guardar nada.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { applyTheme, normalizeTheme, readTheme } from '../../autocann/web/static/js/theme.js';

/** A root element as far as applyTheme can tell: just the attribute. */
function fakeRoot() {
    const attributes = new Map();
    return {
        attributes,
        setAttribute(name, value) { attributes.set(name, String(value)); },
        removeAttribute(name) { attributes.delete(name); },
    };
}

function fakeStorage(initial = {}) {
    const values = new Map(Object.entries(initial));
    return {
        values,
        getItem: key => (values.has(key) ? values.get(key) : null),
        setItem: (key, value) => values.set(key, String(value)),
    };
}

const throwingStorage = {
    getItem() { throw new Error('SecurityError'); },
    setItem() { throw new Error('QuotaExceededError'); },
};

test('only Noche is a theme of its own; anything else is Grafito', () => {
    assert.equal(normalizeTheme('night'), 'night');
    assert.equal(normalizeTheme('graphite'), 'graphite');
    // A value edited by hand, or left by an older build, must not leave the
    // page on a theme that does not exist.
    assert.equal(normalizeTheme('NIGHT'), 'graphite');
    assert.equal(normalizeTheme('solarized'), 'graphite');
    assert.equal(normalizeTheme(null), 'graphite');
});

test('the stored choice is read, and an empty store is Grafito', () => {
    assert.equal(readTheme(fakeStorage({ 'autocann.theme': 'night' })), 'night');
    assert.equal(readTheme(fakeStorage()), 'graphite');
});

test('a browser that refuses storage still gets the default, without an error', () => {
    assert.equal(readTheme(throwingStorage), 'graphite');
});

test('Noche sets the attribute on <html>, Grafito removes it', () => {
    const root = fakeRoot();
    const storage = fakeStorage();
    applyTheme('night', { root, storage });
    assert.equal(root.attributes.get('data-theme'), 'night');

    applyTheme('graphite', { root, storage });
    assert.equal(root.attributes.has('data-theme'), false);
});

test('the choice is remembered under autocann.theme', () => {
    const storage = fakeStorage();
    applyTheme('night', { root: fakeRoot(), storage });
    assert.equal(storage.values.get('autocann.theme'), 'night');
});

test('a storage that throws does not stop the theme from being applied', () => {
    const root = fakeRoot();
    assert.equal(applyTheme('night', { root, storage: throwingStorage }), 'night');
    assert.equal(root.attributes.get('data-theme'), 'night');
});
