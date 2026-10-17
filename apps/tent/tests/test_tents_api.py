"""
The tents API: read and rename the tent, over the real store.

The rig has one tent, id 1, seeded as "Carpa principal".
"""

from __future__ import annotations


def test_read_returns_the_seeded_tent(client):
    response = client.get("/api/tents/1")

    assert response.status_code == 200
    body = response.get_json()
    assert body["id"] == 1
    assert body["name"] == "Carpa principal"
    assert "created_at" in body


def test_read_of_an_unknown_tent_is_404(client):
    response = client.get("/api/tents/99")

    assert response.status_code == 404
    assert "error" in response.get_json()


def test_rename_keeps_the_trimmed_name(client, temp_db):
    response = client.put("/api/tents/1", json={"name": "  Carpa del balcón  "})

    assert response.status_code == 200
    assert response.get_json()["name"] == "Carpa del balcón"
    assert client.get("/api/tents/1").get_json()["name"] == "Carpa del balcón"
    assert temp_db.get_tent(1)["name"] == "Carpa del balcón"


def test_rename_accepts_the_limits(client):
    assert client.put("/api/tents/1", json={"name": "a"}).status_code == 200
    assert client.put("/api/tents/1", json={"name": "a" * 60}).status_code == 200


def test_rename_refuses_a_name_outside_the_limits(client, temp_db):
    before = temp_db.get_tent(1)["name"]

    for name in ["", "   ", "a" * 61, 123, None, ["Carpa"]]:
        response = client.put("/api/tents/1", json={"name": name})
        assert response.status_code == 400, name
        assert response.get_json()["errors"], name

    assert temp_db.get_tent(1)["name"] == before


def test_rename_refuses_a_body_that_is_not_an_object(client):
    assert client.put("/api/tents/1", json=["Carpa"]).status_code == 400
    assert client.put("/api/tents/1", data="Carpa", content_type="text/plain").status_code == 400
    assert client.put("/api/tents/1", json={}).status_code == 400


def test_rename_of_an_unknown_tent_is_404(client):
    assert client.put("/api/tents/99", json={"name": "Nada"}).status_code == 404
    # The tent is checked first, so a bad body on a missing tent is still a 404.
    assert client.put("/api/tents/99", json={"name": ""}).status_code == 404


def test_the_sidebar_shows_the_renamed_tent_on_the_next_load(client):
    client.put("/api/tents/1", json={"name": "Carpa <del balcón>"})

    page = client.get("/").get_data(as_text=True)

    assert 'id="tent-name">Carpa &lt;del balcón&gt;<' in page
    assert "Carpa principal" not in page.split('id="app-sidebar"')[1].split("</aside>")[0]

