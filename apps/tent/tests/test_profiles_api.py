"""
The profiles API: list, read, copy, edit and delete, over the real store.
"""

from __future__ import annotations

import pytest

import autocann.db as db

BUILTIN = db.BUILTIN_PROFILE_ID


def _stage(key="early_veg", **overrides):
    stage = {
        "key": key,
        "name": "Etapa",
        "expected_days": 10,
        "vpd_min": 0.6,
        "vpd_max": 1.0,
        "rh_min": None,
        "rh_max": None,
        "water_only": False,
    }
    stage.update(overrides)
    return stage


def _copy(client, source_id=BUILTIN, name="Mi receta"):
    response = client.post(f"/api/profiles/{source_id}/copy", json={"name": name})
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def _custom_profile(client, name="Mi receta"):
    return _copy(client, BUILTIN, name)["id"]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_list_returns_the_builtin_profile(client):
    response = client.get("/api/profiles")

    assert response.status_code == 200
    body = response.get_json()
    assert body["count"] == len(body["profiles"]) == 1
    assert body["profiles"][0] == {"id": BUILTIN, "name": "Cannabis fotoperiódico", "kind": "builtin"}


def test_read_returns_the_profile_with_its_stages(client):
    response = client.get(f"/api/profiles/{BUILTIN}")

    assert response.status_code == 200
    body = response.get_json()
    assert body["kind"] == "builtin"
    assert body["stages"][0]["key"] == "seedling"
    assert all(isinstance(stage["water_only"], bool) for stage in body["stages"])


def test_read_of_an_unknown_profile_is_404(client):
    response = client.get("/api/profiles/does-not-exist")

    assert response.status_code == 404
    assert "error" in response.get_json()


# ---------------------------------------------------------------------------
# Copying
# ---------------------------------------------------------------------------


def test_copy_creates_an_independent_custom_profile(client):
    builtin_before = client.get(f"/api/profiles/{BUILTIN}").get_json()

    created = _copy(client, BUILTIN, "  Mi receta  ")

    assert created["kind"] == "custom"
    assert created["name"] == "Mi receta"
    assert created["id"] != BUILTIN
    assert [(s["key"], s["vpd_min"], s["vpd_max"], s["rh_min"], s["rh_max"], s["water_only"])
            for s in created["stages"]] == [
        (s["key"], s["vpd_min"], s["vpd_max"], s["rh_min"], s["rh_max"], s["water_only"])
        for s in builtin_before["stages"]
    ]

    # Editing the copy leaves the built-in one as it was.
    client.put(f"/api/profiles/{created['id']}", json={
        "name": "Otra", "stages": [_stage("early_veg", vpd_min=0.8, vpd_max=1.2)],
    })
    assert client.get(f"/api/profiles/{BUILTIN}").get_json() == builtin_before


def test_copy_of_a_custom_profile_is_allowed(client):
    first = _custom_profile(client)

    second = _copy(client, first, "Segunda")

    assert second["kind"] == "custom"
    assert [s["key"] for s in second["stages"]] == [s["key"] for s in client.get(f"/api/profiles/{first}").get_json()["stages"]]


def test_two_copies_with_the_same_name_get_different_ids(client):
    first = _custom_profile(client, "Igual")
    second = _custom_profile(client, "Igual")

    assert first != second


def test_copy_of_an_unknown_profile_is_404(client):
    response = client.post("/api/profiles/does-not-exist/copy", json={"name": "X"})

    assert response.status_code == 404


@pytest.mark.parametrize("payload", [
    {},
    {"name": ""},
    {"name": "   "},
    {"name": "x" * 81},
    {"name": 42},
    ["not", "an", "object"],
])
def test_copy_validates_the_name(client, payload):
    response = client.post(f"/api/profiles/{BUILTIN}/copy", json=payload)

    assert response.status_code == 400
    assert response.get_json()["errors"]


def test_copy_accepts_a_name_of_exactly_80_characters(client):
    assert client.post(f"/api/profiles/{BUILTIN}/copy", json={"name": "x" * 80}).status_code == 201


def test_copy_refusals_carry_a_code_next_to_the_errors(client):
    blank = client.post(f"/api/profiles/{BUILTIN}/copy", json={"name": "  "})
    assert blank.status_code == 400
    assert blank.get_json()["code"] == "invalid_name"
    assert blank.get_json()["errors"]

    not_an_object = client.post(f"/api/profiles/{BUILTIN}/copy", json=["x"])
    assert not_an_object.status_code == 400
    assert not_an_object.get_json()["code"] == "invalid_body"


def test_copy_of_an_unknown_profile_says_unknown_profile(client):
    response = client.post("/api/profiles/does-not-exist/copy", json={"name": "Copia"})

    assert response.status_code == 404
    body = response.get_json()
    assert body["error"] == "profile 'does-not-exist' does not exist"
    assert body["code"] == "unknown_profile"


def test_read_of_an_unknown_profile_says_unknown_profile(client):
    response = client.get("/api/profiles/does-not-exist")

    assert response.status_code == 404
    assert response.get_json()["code"] == "unknown_profile"


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


def test_edit_replaces_the_name_and_every_stage(client):
    profile_id = _custom_profile(client)
    stages = [
        _stage("early_veg", name="Crecimiento", vpd_min=0.8, vpd_max=1.1),
        _stage("flowering", name="Floración", expected_days=None, vpd_min=None, vpd_max=None,
               rh_min=40.0, rh_max=50.0, water_only=True),
    ]

    response = client.put(f"/api/profiles/{profile_id}", json={"name": "Nueva", "stages": stages})

    assert response.status_code == 200
    body = response.get_json()
    assert body["name"] == "Nueva"
    assert [s["key"] for s in body["stages"]] == ["early_veg", "flowering"]
    assert [s["position"] for s in body["stages"]] == [0, 1]
    assert body["stages"][1]["water_only"] is True
    assert body["stages"][1]["rh_min"] == 40.0
    assert client.get(f"/api/profiles/{profile_id}").get_json() == body


def test_edit_of_a_builtin_profile_is_403_even_with_a_valid_body(client):
    before = client.get(f"/api/profiles/{BUILTIN}").get_json()

    response = client.put(f"/api/profiles/{BUILTIN}", json={"name": "Mía", "stages": [_stage()]})

    assert response.status_code == 403
    assert client.get(f"/api/profiles/{BUILTIN}").get_json() == before


def test_edit_of_an_unknown_profile_is_404(client):
    response = client.put("/api/profiles/does-not-exist", json={"name": "X", "stages": [_stage()]})

    assert response.status_code == 404
    assert response.get_json()["code"] == "unknown_profile"


def test_edit_refusals_carry_a_code(client):
    builtin = client.put(f"/api/profiles/{BUILTIN}", json={"name": "Mía", "stages": [_stage()]})
    assert builtin.status_code == 403
    assert builtin.get_json()["code"] == "read_only_profile"

    profile_id = _custom_profile(client)
    blank_name = client.put(f"/api/profiles/{profile_id}", json={"name": " ", "stages": [_stage()]})
    assert blank_name.status_code == 400
    assert blank_name.get_json()["code"] == "invalid_name"

    bad_stages = client.put(f"/api/profiles/{profile_id}", json={"name": "X", "stages": [_stage(vpd_min=1.2, vpd_max=0.8)]})
    assert bad_stages.status_code == 400
    assert bad_stages.get_json()["code"] == "invalid_stages"
    assert bad_stages.get_json()["errors"]

    not_an_object = client.put(f"/api/profiles/{profile_id}", json=[_stage()])
    assert not_an_object.get_json()["code"] == "invalid_body"


def test_invalid_stages_come_back_as_a_list_of_every_error(client):
    profile_id = _custom_profile(client)
    before = client.get(f"/api/profiles/{profile_id}").get_json()
    stages = [
        _stage("early_veg", vpd_min=1.2, vpd_max=0.8),
        _stage("early_veg", expected_days=None),
        _stage("late_veg", expected_days=None, vpd_min=None, vpd_max=None),
    ]

    response = client.put(f"/api/profiles/{profile_id}", json={"name": "X", "stages": stages})

    assert response.status_code == 400
    errors = response.get_json()["errors"]
    assert isinstance(errors, list)
    assert any("VPD band" in error for error in errors)
    assert any("more than one stage" in error for error in errors)
    assert any("only the last stage may be open-ended" in error for error in errors)
    assert client.get(f"/api/profiles/{profile_id}").get_json() == before


def test_an_empty_stage_list_is_400(client):
    profile_id = _custom_profile(client)

    response = client.put(f"/api/profiles/{profile_id}", json={"name": "X", "stages": []})

    assert response.status_code == 400
    assert response.get_json()["errors"]


@pytest.mark.parametrize("stage", [
    _stage(key="Early Veg"),
    _stage(key=""),
    _stage(key="x" * 33),
    _stage(name=""),
    _stage(expected_days="10"),
    _stage(expected_days=True),
    _stage(vpd_min="0.6"),
    _stage(vpd_max=float("nan")),
    _stage(water_only="yes"),
    "early_veg",
])
def test_stage_types_are_checked_before_the_rules(client, stage):
    profile_id = _custom_profile(client)

    response = client.put(f"/api/profiles/{profile_id}", json={"name": "X", "stages": [stage]})

    assert response.status_code == 400
    assert response.get_json()["errors"]


def test_stages_must_be_a_list(client):
    profile_id = _custom_profile(client)

    response = client.put(f"/api/profiles/{profile_id}", json={"name": "X", "stages": {"key": "early_veg"}})

    assert response.status_code == 400


@pytest.mark.parametrize("name", [None, "", "  ", "x" * 81, 7])
def test_edit_validates_the_name(client, name):
    profile_id = _custom_profile(client)

    response = client.put(f"/api/profiles/{profile_id}", json={"name": name, "stages": [_stage()]})

    assert response.status_code == 400
    assert response.get_json()["errors"]


def test_a_body_that_is_not_an_object_is_400(client):
    profile_id = _custom_profile(client)

    response = client.put(f"/api/profiles/{profile_id}", json=[_stage()])

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------


def test_delete_removes_a_custom_profile_and_its_stages(client, temp_db):
    profile_id = _custom_profile(client)

    response = client.delete(f"/api/profiles/{profile_id}")

    assert response.status_code == 204
    assert client.get(f"/api/profiles/{profile_id}").status_code == 404
    conn = temp_db._open()
    try:
        assert conn.execute("SELECT COUNT(*) FROM profile_stages WHERE profile_id = ?", (profile_id,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_delete_of_a_builtin_profile_is_403(client):
    response = client.delete(f"/api/profiles/{BUILTIN}")

    assert response.status_code == 403
    assert client.get(f"/api/profiles/{BUILTIN}").status_code == 200


def test_delete_of_an_unknown_profile_is_404(client):
    assert client.delete("/api/profiles/does-not-exist").status_code == 404


def test_delete_is_409_while_an_active_grow_uses_the_profile(client, temp_db):
    profile_id = _custom_profile(client)
    grow_id = temp_db.create_grow("Cultivo", "early_veg", profile_id=profile_id)
    assert grow_id is not None

    response = client.delete(f"/api/profiles/{profile_id}")

    assert response.status_code == 409
    assert "error" in response.get_json()
    assert client.get(f"/api/profiles/{profile_id}").status_code == 200

    temp_db.end_grow(grow_id)

    assert client.delete(f"/api/profiles/{profile_id}").status_code == 204


# ---------------------------------------------------------------------------
# Editing never reaches a grow
# ---------------------------------------------------------------------------


def test_editing_a_profile_does_not_change_an_existing_grow(client, temp_db):
    profile_id = _custom_profile(client)
    grow_id = temp_db.create_grow("Cultivo", "early_veg", profile_id=profile_id)
    grow_stages_before = temp_db.get_grow_stages(grow_id)
    assert grow_stages_before

    response = client.put(f"/api/profiles/{profile_id}", json={
        "name": "Cambiada",
        "stages": [_stage("early_veg", vpd_min=0.9, vpd_max=1.4), _stage("flowering", vpd_min=1.0, vpd_max=1.5)],
    })

    assert response.status_code == 200
    assert temp_db.get_grow_stages(grow_id) == grow_stages_before
    assert [s["key"] for s in client.get(f"/api/profiles/{profile_id}").get_json()["stages"]] == [
        "early_veg", "flowering",
    ]


# ---------------------------------------------------------------------------
# Auth: the same PIN gate as the rest of the API
# ---------------------------------------------------------------------------


def test_profile_writes_need_the_pin_when_one_is_set(monkeypatch, temp_db):
    import autocann.web.app as web
    from tests.conftest import FakeRedis

    monkeypatch.setattr(web, "pooled_client", lambda *args, **kwargs: FakeRedis())
    monkeypatch.setenv("AUTOCANN_PIN", "4821")
    app = web.create_app()
    app.config.update(TESTING=True)
    locked = app.test_client()

    assert locked.get("/api/profiles").status_code == 401
    assert locked.post(f"/api/profiles/{BUILTIN}/copy", json={"name": "X"}).status_code == 401
    assert locked.delete(f"/api/profiles/{BUILTIN}").status_code == 401
