"""
The "Nuevo cultivo" form's request, on the built-in profile, behaves exactly as
the request it replaced: same stages, same starting stage, same answer.
"""

from __future__ import annotations

from autocann.store.grow_stages import get_grow_stages


def _create(client, body):
    response = client.post("/api/grows", json=body)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["grow_id"]


def _stage_rows(grow_id):
    return [
        {key: stage[key] for key in ("key", "name", "expected_days", "vpd_min", "vpd_max", "rh_min", "rh_max", "water_only")}
        for stage in get_grow_stages(grow_id)
    ]


def test_form_with_the_builtin_profile_creates_the_grow_it_did_before(client, temp_db):
    # The form's body as the built-in profile sends it: the old fields and the profile id.
    form_body = {"name": "Cultivo #2", "stage": "seedling", "notes": ""}
    # The same request without the profile id: the grow the server made before profiles existed.
    without_profile = _create(client, dict(form_body))
    before = temp_db.get_active_grow()
    before_stages = _stage_rows(without_profile)

    with_profile = _create(client, {**form_body, "profile_id": temp_db.BUILTIN_PROFILE_ID})
    after = temp_db.get_active_grow()

    assert with_profile != without_profile
    assert after["id"] == with_profile
    assert after["profile_id"] == temp_db.BUILTIN_PROFILE_ID == before["profile_id"]
    assert after["stage"] == before["stage"] == "seedling"
    assert after["name"] == before["name"] == "Cultivo #2"
    assert after["tent_id"] == before["tent_id"] == 1
    assert _stage_rows(with_profile) == before_stages


def test_form_with_the_builtin_profile_starts_at_the_stage_it_sent(client, temp_db):
    _create(client, {"name": "Floración", "stage": "flowering", "notes": "", "profile_id": temp_db.BUILTIN_PROFILE_ID})

    assert temp_db.get_active_grow()["stage"] == "flowering"
