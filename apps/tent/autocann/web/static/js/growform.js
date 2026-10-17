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

/** The body POST /api/grows takes. A built-in profile sends the same fields as before, plus its id. */
export function buildGrowRequest({ name, profileId, stage, notes }) {
    return { name, stage, notes, profile_id: profileId };
}

// ---------------------------------------------------------------------------
// The form
// ---------------------------------------------------------------------------

let stages = [];
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

function showStageHint() {
    const stage = stages.find(item => item.key === field('newGrowStage').value);
    field('newGrowStageHint').textContent = stageBandHint(stage);
}

/** The profile the form is set to, for the request. */
export function selectedProfile() {
    return field('newGrowProfile')?.value ?? '';
}

/** Fills the stage select from the chosen profile. */
async function showProfileStages() {
    const current = ++choice;
    const profileId = selectedProfile();
    const profile = profileId ? await apiGet(`/api/profiles/${encodeURIComponent(profileId)}`) : null;
    if (current !== choice) return;

    stages = profile?.stages ?? [];
    fillSelect(field('newGrowStage'), stageOptions(stages), defaultStage(stages));
    if (!stages.length) {
        field('newGrowStageHint').textContent = 'No se pudieron cargar las etapas de este perfil.';
        return;
    }
    showStageHint();
}

/** Fills the profile select, then the stages of the first one. */
async function loadProfiles() {
    const data = await apiGet('/api/profiles');
    if (!data) {
        fillSelect(field('newGrowProfile'), []);
        field('newGrowProfileHint').textContent = 'No se pudieron cargar los perfiles.';
        return;
    }
    fillSelect(field('newGrowProfile'), profileOptions(data.profiles));
    await showProfileStages();
}

/** Opens "Nuevo cultivo" with its name and notes empty and the profile's stages loaded. */
export function openNewGrowForm() {
    field('newGrowName').value = '';
    field('newGrowNotes').value = '';
    field('newGrowStageHint').textContent = '';
    field('newGrowProfileHint').textContent = '';
    fillSelect(field('newGrowProfile'), [{ value: '', label: 'Cargando perfiles…' }]);
    fillSelect(field('newGrowStage'), []);
    showModal('newGrowModal');
    loadProfiles();
}

/** Wires the two selects and the stage hint. Once, at start-up. */
export function wireNewGrowForm() {
    if (wired) return;
    wired = true;
    field('newGrowProfile')?.addEventListener('change', showProfileStages);
    field('newGrowStage')?.addEventListener('change', showStageHint);
}
