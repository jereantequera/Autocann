"""
Feed chart and irrigation log, from the database layer up through the API.

The pure scheduling rules are covered in test_feeding.py; what is checked here
is persistence, the stage stamping the server does on the grower's behalf, and
the editor's full-replace save.
"""

from __future__ import annotations

import pytest

from autocann.db import (
    delete_irrigation,
    get_feed_plan,
    get_irrigations,
    last_irrigation_litres,
    record_irrigation,
    replace_feed_plan,
)


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        value = self.store.get(key)
        return value.encode() if isinstance(value, str) else value

    def set(self, key, value, ex=None):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def client(monkeypatch, temp_db):
    import autocann.web.app as web

    monkeypatch.setattr(web.redis, "Redis", lambda **kwargs: FakeRedis())
    app = web.create_app()
    app.config.update(TESTING=True)
    return app.test_client()


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------


def test_an_empty_database_gets_the_default_chart(temp_db):
    plan = get_feed_plan()
    assert [p["name"] for p in plan][:3] == [
        "pH Perfect Grow", "pH Perfect Micro", "pH Perfect Bloom",
    ]
    assert {p["unit"] for p in plan} == {"ml/L", "g/L"}


def test_the_base_ramp_reaches_full_strength_by_late_veg(temp_db):
    grow = next(p for p in get_feed_plan() if p["name"] == "pH Perfect Grow")
    assert grow["doses"]["early_veg"] == {1: 1, 2: 2, 3: 3}
    assert set(grow["doses"]["late_veg"].values()) == {4}


def test_the_last_week_of_flowering_is_left_empty_for_the_flush(temp_db):
    for product in get_feed_plan():
        assert 8 not in product["doses"].get("flowering", {})


def test_a_second_process_waits_for_the_seed_instead_of_repeating_it(temp_db):
    """
    systemd starts the web app and the control loop together and both call
    ensure_schema(). If the product count is read outside a write lock, both
    can see an empty table and seed it, leaving every product in twice.

    Sequenced by hand rather than by racing threads: a thread race serialises
    on the connection pragmas and passes either way, which would make this
    test unable to fail. Here one connection deliberately holds the seed open
    while the other tries, which is the state the lock has to handle.
    """
    import threading

    # Start from an empty chart, as a fresh install would.
    assert replace_feed_plan([])

    holder = temp_db._open()
    holder.execute("BEGIN IMMEDIATE")
    holder.execute(
        "INSERT INTO feed_products (name, unit, position) VALUES ('pH Perfect Grow', 'ml/L', 0)"
    )

    result = {}

    def seed_from_another_process():
        conn = temp_db._open()
        try:
            temp_db._seed_feed_plan(conn)
            result["ok"] = True
        except Exception as error:
            result["error"] = error
        finally:
            conn.close()

    second = threading.Thread(target=seed_from_another_process)
    second.start()
    # Let it reach the lock, then release it as the first process would.
    second.join(timeout=1)
    assert second.is_alive(), "el segundo proceso no esperó el lock"

    holder.commit()
    holder.close()
    second.join(timeout=15)
    assert not second.is_alive()

    assert "error" not in result, result.get("error")
    names = [p["name"] for p in get_feed_plan()]
    assert names == ["pH Perfect Grow"], "el segundo proceso volvió a sembrar"


def test_a_deleted_product_does_not_come_back(temp_db):
    plan = get_feed_plan()
    assert replace_feed_plan([p for p in plan if p["name"] != "Overdrive"])

    temp_db._schema_ready = False
    temp_db.ensure_schema()
    assert "Overdrive" not in [p["name"] for p in get_feed_plan()]


# --------------------------------------------------------------------------
# Editing the chart
# --------------------------------------------------------------------------


def test_saving_the_grid_keeps_ids_renames_and_reorders(temp_db):
    plan = get_feed_plan()
    first_id = plan[0]["id"]

    plan[0]["name"] = "Grow genérico"
    plan.reverse()
    assert replace_feed_plan(plan)

    saved = get_feed_plan()
    assert saved[-1]["id"] == first_id
    assert saved[-1]["name"] == "Grow genérico"


def test_a_product_without_an_id_is_created(temp_db):
    plan = get_feed_plan()
    plan.append({"name": "Quitosano", "unit": "ml/L", "doses": {"flowering": {2: 1.5}}})
    assert replace_feed_plan(plan)

    new = next(p for p in get_feed_plan() if p["name"] == "Quitosano")
    assert new["id"]
    assert new["doses"]["flowering"] == {2: 1.5}


def test_zero_and_unreadable_doses_are_not_stored(temp_db):
    assert replace_feed_plan([
        {"name": "Raro", "unit": "ml/L",
         "doses": {"flowering": {1: 0, 2: "mucho", 3: 2, 0: 5}}},
    ])
    assert get_feed_plan()[0]["doses"]["flowering"] == {3: 2}


def test_an_unknown_unit_falls_back_to_millilitres(temp_db):
    assert replace_feed_plan([{"name": "X", "unit": "cucharadas", "doses": {}}])
    assert get_feed_plan()[0]["unit"] == "ml/L"


def test_a_blank_name_is_not_stored_as_empty(temp_db):
    assert replace_feed_plan([{"name": "   ", "unit": "ml/L", "doses": {}}])
    assert get_feed_plan()[0]["name"] == "Sin nombre"


def test_clearing_the_chart_leaves_no_orphan_doses(temp_db):
    assert replace_feed_plan([])
    assert get_feed_plan() == []

    conn = temp_db._open()
    leftover = conn.execute("SELECT COUNT(*) FROM feed_plan").fetchone()[0]
    conn.close()
    assert leftover == 0


# --------------------------------------------------------------------------
# Irrigation log
# --------------------------------------------------------------------------


def test_a_watering_comes_back_with_what_went_into_it(temp_db):
    irrigation_id = record_irrigation(
        grow_id=1, litres=12, date="2026-10-11", ph=6.0, ec=1.8,
        stage="flowering", week=3, notes="sin novedad",
        items=[{"name": "pH Perfect Grow", "unit": "ml/L", "dose": 4, "product_id": 1}],
    )
    assert irrigation_id

    logged = get_irrigations(1)[0]
    assert logged["litres"] == 12
    assert logged["ph"] == 6.0
    assert logged["stage"] == "flowering"
    assert logged["notes"] == "sin novedad"
    assert logged["items"][0]["name"] == "pH Perfect Grow"
    assert logged["items"][0]["on_plan"] is True


def test_a_plain_water_watering_is_worth_recording(temp_db):
    assert record_irrigation(grow_id=1, litres=10, items=[])
    assert get_irrigations(1)[0]["items"] == []


def test_renaming_a_product_does_not_rewrite_the_log(temp_db):
    """What the log says was poured has to stay true after the chart changes."""
    record_irrigation(grow_id=1, litres=10,
                      items=[{"name": "Big Bud", "unit": "ml/L", "dose": 2, "product_id": 7}])

    plan = get_feed_plan()
    for product in plan:
        if product["name"] == "Big Bud":
            product["name"] = "Otro engordante"
    replace_feed_plan(plan)

    assert get_irrigations(1)[0]["items"][0]["name"] == "Big Bud"


def test_deleting_a_watering_takes_its_items_with_it(temp_db):
    irrigation_id = record_irrigation(
        grow_id=1, litres=10,
        items=[{"name": "Bud Candy", "unit": "ml/L", "dose": 2}],
    )
    assert delete_irrigation(irrigation_id)
    assert get_irrigations(1) == []

    conn = temp_db._open()
    leftover = conn.execute("SELECT COUNT(*) FROM irrigation_items").fetchone()[0]
    conn.close()
    assert leftover == 0


def test_deleting_a_watering_that_is_not_there_says_so(temp_db):
    assert delete_irrigation(9999) is False


def test_the_last_volume_is_remembered_for_the_next_one(temp_db):
    assert last_irrigation_litres(1) == 10.0  # nothing logged yet
    record_irrigation(grow_id=1, litres=7.5, items=[])
    assert last_irrigation_litres(1) == 7.5


def test_waterings_come_back_newest_first(temp_db):
    for date in ("2026-10-01", "2026-10-09", "2026-10-05"):
        record_irrigation(grow_id=1, litres=10, date=date, items=[])
    assert [i["date"] for i in get_irrigations(1)] == [
        "2026-10-09", "2026-10-05", "2026-10-01",
    ]


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def test_the_plan_endpoint_serves_the_grid_shape(client):
    payload = client.get("/api/feed-plan").get_json()
    assert payload["weeks_per_stage"] == {
        "early_veg": 3, "late_veg": 4, "flowering": 8, "dry": 2,
    }
    assert payload["columns"][0] == {"stage": "early_veg", "week": 1}
    assert len(payload["products"]) == 10


def test_saving_the_plan_rejects_a_body_without_products(client):
    assert client.put("/api/feed-plan", json={}).status_code == 400
    assert client.put("/api/feed-plan", json={"products": "todos"}).status_code == 400


def test_saving_the_plan_returns_what_was_stored(client):
    response = client.put("/api/feed-plan", json={
        "products": [{"name": "Único", "unit": "g/L", "doses": {"flowering": {1: 3}}}],
    })
    assert response.status_code == 200
    assert [p["name"] for p in response.get_json()["products"]] == ["Único"]


def test_today_reads_the_stage_from_the_active_grow(client, temp_db):
    """The default grow starts in early_veg on day 1, so week 1 is due."""
    payload = client.get("/api/feeding/today?litres=10").get_json()
    assert payload["stage"] == "early_veg"
    assert payload["week"] == 1
    assert payload["flush"] is False

    by_name = {item["name"]: item for item in payload["items"]}
    assert by_name["pH Perfect Grow"]["total"] == 10
    assert by_name["Micorrizas"]["unit"] == "g/L"


def test_today_scales_to_the_requested_volume(client):
    payload = client.get("/api/feeding/today?litres=25").get_json()
    assert payload["items"][0]["total"] == 25


def test_today_reports_a_flush_week_as_such(client, temp_db):
    temp_db.update_grow_stage(1, "dry")
    payload = client.get("/api/feeding/today").get_json()
    assert payload["items"] == []
    assert payload["flush"] is True


def test_logging_a_watering_stamps_the_stage_server_side(client, temp_db):
    """The browser does not get to decide what stage the plant was in."""
    response = client.post("/api/irrigations", json={
        "litres": 10,
        "stage": "flowering",  # a lie; the active grow is in early_veg
        "items": [{"name": "pH Perfect Grow", "unit": "ml/L", "dose": 1}],
    })
    assert response.status_code == 201

    logged = client.get("/api/irrigations").get_json()["irrigations"][0]
    assert logged["stage"] == "early_veg"
    assert logged["week"] == 1


@pytest.mark.parametrize("body", [
    {},
    {"litres": 0},
    {"litres": -3},
    {"litres": "un balde"},
    {"litres": 10, "items": "todos"},
])
def test_a_malformed_watering_is_refused(client, body):
    assert client.post("/api/irrigations", json=body).status_code == 400


def test_a_watering_can_be_deleted_through_the_api(client):
    created = client.post("/api/irrigations", json={"litres": 10, "items": []}).get_json()
    assert client.delete(f"/api/irrigations/{created['irrigation_id']}").status_code == 200
    assert client.delete(f"/api/irrigations/{created['irrigation_id']}").status_code == 404
