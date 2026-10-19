/**
 * Tests for the server-error line a form shows (static/js/errors.js).
 *
 * Run with: node --test tests/js/*.test.mjs
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { FALLBACK_MESSAGE, errorMessage } from '../../autocann/web/static/js/errors.js';

test('each code the forms can show maps to its Spanish line', () => {
    assert.equal(errorMessage({ code: 'invalid_stage' }), 'Esa etapa no existe en el perfil elegido.');
    assert.equal(errorMessage({ code: 'no_active_grow' }), 'No hay un cultivo activo.');
    assert.equal(errorMessage({ code: 'unknown_profile' }), 'Ese perfil ya no existe.');
    assert.equal(errorMessage({ code: 'invalid_name' }), 'Ponele un nombre.');
    assert.equal(errorMessage({ code: 'invalid_body' }), 'Faltan datos en el formulario.');
    assert.equal(errorMessage({ code: 'missing_field' }), 'Faltan datos en el formulario.');
    assert.equal(errorMessage({ code: 'invalid_interval' }), 'Ese intervalo no existe.');
});

test('the relay, watering, feed plan and calibration codes map to their Spanish lines', () => {
    assert.equal(errorMessage({ code: 'invalid_mode' }), 'Ese modo no existe.');
    assert.equal(errorMessage({ code: 'unknown_output' }), 'Esa salida no existe.');
    assert.equal(errorMessage({ code: 'invalid_state' }), 'Elegí encendido o apagado.');
    assert.equal(errorMessage({ code: 'invalid_duration' }), 'La duración no es válida.');
    assert.equal(errorMessage({ code: 'not_paused' }),
        'Los relés sólo se mueven a mano con el sistema en pausa.');
    assert.equal(errorMessage({ code: 'invalid_litres' }), 'Los litros tienen que ser un número mayor a 0.');
    assert.equal(errorMessage({ code: 'invalid_items' }), 'Los productos del riego no tienen un formato válido.');
    assert.equal(errorMessage({ code: 'irrigation_not_found' }), 'Ese riego ya no existe.');
    assert.equal(errorMessage({ code: 'invalid_offset' }), 'El ajuste tiene que ser un número.');
    assert.equal(errorMessage({ code: 'offset_out_of_range' }), 'Ese ajuste está fuera de rango.');
    assert.equal(errorMessage({ code: 'save_failed' }), 'No se pudo guardar. Probá de nuevo.');
});

test('the English text is never shown, even when the code is unknown', () => {
    const result = { error: 'Invalid stage. Use one of: a, b', code: 'something_new' };
    assert.equal(errorMessage(result), FALLBACK_MESSAGE);
    assert.equal(FALLBACK_MESSAGE, 'No se pudo guardar. Probá de nuevo.');
});

test('a response without a code gets the generic line, whatever its text', () => {
    assert.equal(errorMessage({ error: 'No active grow found' }), FALLBACK_MESSAGE);
    assert.equal(errorMessage({ errors: ['name must be a non-empty string'] }), FALLBACK_MESSAGE);
    assert.equal(errorMessage({}), FALLBACK_MESSAGE);
});

test('a missing, null or non-string body or code gets the generic line', () => {
    assert.equal(errorMessage(undefined), FALLBACK_MESSAGE);
    assert.equal(errorMessage(null), FALLBACK_MESSAGE);
    assert.equal(errorMessage({ code: null }), FALLBACK_MESSAGE);
    assert.equal(errorMessage({ code: 42 }), FALLBACK_MESSAGE);
});

test('object prototype names are not codes', () => {
    assert.equal(errorMessage({ code: 'toString' }), FALLBACK_MESSAGE);
    assert.equal(errorMessage({ code: '__proto__' }), FALLBACK_MESSAGE);
    assert.equal(errorMessage({ code: 'constructor' }), FALLBACK_MESSAGE);
});
