/**
 * Tests for the helpers behind "Nuevo cultivo" (growform.js): the profile
 * list, the stages of a profile, and the band hint under the stage select.
 *
 * Run with: node --test tests/js/*.test.mjs
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    buildGrowRequest, defaultStage, profileOptions, stageBandHint, stageOptions,
} from '../../autocann/web/static/js/growform.js';

const BUILTIN = { id: 'cannabis-photoperiod', name: 'Cannabis fotoperiódico', kind: 'builtin' };
const CUSTOM = { id: 'custom-propagation', name: 'Esquejes', kind: 'custom' };

const SEEDLING = {
    key: 'seedling', name: 'Plántula', expected_days: 14,
    vpd_min: 0.4, vpd_max: 0.8, rh_min: null, rh_max: null, water_only: true,
};
const EARLY_VEG = {
    key: 'early_veg', name: 'Vegetativo temprano', expected_days: 21,
    vpd_min: 0.6, vpd_max: 1.0, rh_min: null, rh_max: null, water_only: false,
};
const DRY = {
    key: 'dry', name: 'Secado', expected_days: 10,
    vpd_min: null, vpd_max: null, rh_min: 60, rh_max: 65, water_only: false,
};

test('the profile list puts the built-in profile first, whatever order it came in', () => {
    assert.deepEqual(profileOptions([CUSTOM, BUILTIN]), [
        { value: 'cannabis-photoperiod', label: 'Cannabis fotoperiódico' },
        { value: 'custom-propagation', label: 'Esquejes' },
    ]);
});

test('the profile list is empty when the server had none, and never throws', () => {
    assert.deepEqual(profileOptions([]), []);
    assert.deepEqual(profileOptions(undefined), []);
});

test('the stage options follow the profile, in its order', () => {
    assert.deepEqual(stageOptions([SEEDLING, EARLY_VEG]), [
        { value: 'seedling', label: 'Plántula' },
        { value: 'early_veg', label: 'Vegetativo temprano' },
    ]);
    assert.deepEqual(stageOptions([DRY]), [{ value: 'dry', label: 'Secado' }]);
    assert.deepEqual(stageOptions(undefined), []);
});

test('a new grow starts at the first stage of its profile', () => {
    assert.equal(defaultStage([SEEDLING, EARLY_VEG]), 'seedling');
    assert.equal(defaultStage([]), '');
});

test('the hint shows the VPD band of a stage, with a decimal comma', () => {
    assert.equal(stageBandHint(EARLY_VEG), 'VPD objetivo 0,6 – 1,0 kPa');
});

test('a stage with only a humidity band shows that band instead', () => {
    assert.equal(stageBandHint(DRY), 'Humedad objetivo 60 – 65 %');
});

test('a water-only stage says so after its band', () => {
    assert.equal(stageBandHint(SEEDLING), 'VPD objetivo 0,4 – 0,8 kPa · Sólo agua, sin nutrientes');
});

test('the hint is empty without a stage, and says so when a stage has no band', () => {
    assert.equal(stageBandHint(undefined), '');
    assert.equal(stageBandHint({ key: 'x', name: 'X', vpd_min: null, vpd_max: null, rh_min: null, rh_max: null }),
        'Sin banda objetivo');
});

test('the request is the same fields as before, plus the profile', () => {
    assert.deepEqual(
        buildGrowRequest({ name: 'Cultivo #2', profileId: BUILTIN.id, stage: 'seedling', notes: '' }),
        { name: 'Cultivo #2', stage: 'seedling', notes: '', profile_id: 'cannabis-photoperiod' },
    );
});
