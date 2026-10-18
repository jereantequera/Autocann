/**
 * "Nuevo cultivo": the crop profile to follow, the stage to start at, and the
 * band that stage runs at.
 *
 * The helpers at the top are pure and tested in tests/js/growform.test.mjs.
 * The form below them reads /api/profiles and /api/profiles/<id> and fills the
 * two selects. The server still has the last word: POST /api/grows checks the
 * stage against the profile it is sent with.
 */

import { apiGet } from './api.js';
import { showModal } from './modals.js';
import { vpdLabel } from './grow-model.js';

/** Built-in profiles first, then the rest, each group in the order the server gave. */
export function profileOptions(profiles) {
    return [...(profiles ?? [])]
        .sort((a, b) => Number(b.kind === 'builtin') - Number(a.kind === 'builtin'))
        .map(profile => ({ value: profile.id, label: profile.name }));
}

/** The stages of a profile, in their order, as select options. */
export function stageOptions(stages) {
    return (stages ?? []).map(stage => ({ value: stage.key, label: stage.name }));
}

/** The stage a new grow starts at unless the person picks another: the profile's first. */
export function defaultStage(stages) {
    return stages?.[0]?.key ?? '';
}

/** A number as this UI writes it: 0.6 becomes "0,6", 60 stays "60". */
function comma(value) {
    return String(value).replace('.', ',');
}

/**
 * The one line under the stage select: the band the stage runs at.
 *
 * VPD when the stage has a VPD band, otherwise the air-humidity band. A
 * water-only stage says so, because nothing is dosed in it.
 */
export function stageBandHint(stage) {
    if (!stage) return '';
    let band = vpdLabel({ min: stage.vpd_min, max: stage.vpd_max });
    if (band) {
        band = `VPD objetivo ${band}`;
    } else if (Number.isFinite(stage.rh_min) && Number.isFinite(stage.rh_max)) {
        band = `Humedad objetivo ${comma(stage.rh_min)} – ${comma(stage.rh_max)} %`;
    } else {
        band = 'Sin banda objetivo';
    }
    return stage.water_only ? `${band} · Sólo agua, sin nutrientes` : band;
}

/**
 * What the form shows while the stage list is not usable: whether "Crear
 * Cultivo" is enabled, the line under the stage select, and whether that line
 * offers a retry. Loaded with at least one stage means enabled and no line.
 *
 * A profile change puts the form back to `loading`, even when the old profile's
 * stages are still in memory, so those stages cannot be submitted.
 */
export function formState({ loading, failed, stagesCount }) {
    if (loading) return { submitEnabled: false, hint: 'Cargando etapas…', retry: false };
    if (failed || !(stagesCount >= 1)) {
        return { submitEnabled: false, hint: 'No se pudieron cargar las etapas', retry: true };
    }
    return { submitEnabled: true, hint: '', retry: false };
}

/** The body POST /api/grows takes. A built-in profile sends the same fields as before, plus its id. */
export function buildGrowRequest({ name, profileId, stage, notes }) {
    return { name, stage, notes, profile_id: profileId };
}

// ---------------------------------------------------------------------------
// The form
// ---------------------------------------------------------------------------

let stages = [];
/** Whether the stages of the chosen profile are being fetched, and whether that failed. */
let stageStatus = { loading: true, failed: false };
/** Bumped on every profile change, so a slow answer for an old choice is dropped. */
let choice = 0;
let wired = false;

function field(id) {
    return document.getElementById(id);
}

function fillSelect(select, options, selected = '') {
    select.replaceChildren(...options.map(option => new Option(option.label, option.value)));
    select.value = selected || (options[0]?.value ?? '');
}

/** The profile the form is set to, for the request. */
export function selectedProfile() {
    return field('newGrowProfile')?.value ?? '';
}

/** Whether "Crear Cultivo" may send the grow now: the stages are in and usable. */
export function canSubmitGrow() {
    return formState({ ...stageStatus, stagesCount: stages.length }).submitEnabled;
}

/** Shows the server's answer to a failed create inside the modal. */
export function showGrowError(message) {
    const error = field('newGrowError');
    error.textContent = message;
    error.hidden = false;
}

export function clearGrowError() {
    field('newGrowError').hidden = true;
}

/** Applies formState to the submit button, the retry button and the stage hint line. */
function renderFormState() {
    const state = formState({ ...stageStatus, stagesCount: stages.length });
    field('newGrowSubmit').disabled = !state.submitEnabled;
    field('newGrowRetry').hidden = !state.retry;
    const stage = stages.find(item => item.key === field('newGrowStage').value);
    field('newGrowStageHint').textContent = state.hint || stageBandHint(stage);
}

/** Fills the stage select from the chosen profile. */
async function showProfileStages() {
    const current = ++choice;
    const profileId = selectedProfile();
    stages = [];
    stageStatus = { loading: true, failed: false };
    fillSelect(field('newGrowStage'), []);
    renderFormState();

    // A key per change: the de-duplication in apiGet would otherwise answer a
    // second change with null while the first request is still out.
    const profile = profileId
        ? await apiGet(`/api/profiles/${encodeURIComponent(profileId)}`, { key: `profile:${current}` })
        : null;
    if (current !== choice) return;

    stages = profile?.stages ?? [];
    stageStatus = { loading: false, failed: !stages.length };
    fillSelect(field('newGrowStage'), stageOptions(stages), defaultStage(stages));
    renderFormState();
}

/** Fills the profile select, then the stages of the first one. */
async function loadProfiles() {
    stageStatus = { loading: true, failed: false };
    renderFormState();
    const data = await apiGet('/api/profiles');
    if (!data) {
        fillSelect(field('newGrowProfile'), []);
        field('newGrowProfileHint').textContent = 'No se pudieron cargar los perfiles.';
        stages = [];
        stageStatus = { loading: false, failed: true };
        fillSelect(field('newGrowStage'), []);
        renderFormState();
        return;
    }
    field('newGrowProfileHint').textContent = '';
    fillSelect(field('newGrowProfile'), profileOptions(data.profiles));
    await showProfileStages();
}

/** Retries whatever failed: the profile list if it never came, else the stages. */
function retryLoad() {
    if (selectedProfile()) showProfileStages();
    else loadProfiles();
}

/** Opens "Nuevo cultivo" with its name and notes empty and the profile's stages loaded. */
export function openNewGrowForm() {
    field('newGrowName').value = '';
    field('newGrowNotes').value = '';
    field('newGrowProfileHint').textContent = '';
    clearGrowError();
    choice += 1;
    stages = [];
    fillSelect(field('newGrowProfile'), [{ value: '', label: 'Cargando perfiles…' }]);
    fillSelect(field('newGrowStage'), []);
    showModal('newGrowModal');
    loadProfiles();
}

/** Wires the selects and the retry button. Once, at start-up. */
export function wireNewGrowForm() {
    if (wired) return;
    wired = true;
    field('newGrowProfile')?.addEventListener('change', showProfileStages);
    field('newGrowStage')?.addEventListener('change', renderFormState);
    field('newGrowRetry')?.addEventListener('click', retryLoad);
}
