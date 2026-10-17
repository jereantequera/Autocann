"""
The irrigation schedule API: GET /api/irrigation/next and PUT
/api/irrigation/interval, from the request through to the database.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from autocann.time import ARGENTINA_TZ


def _today():
    return datetime.now(ARGENTINA_TZ).date()


def _ago(days: int) -> str:
    return (_today() - timedelta(days=days)).isoformat()


def _grow_id(temp_db) -> int:
    return temp_db.get_active_grow()["id"]


def _put(client, body):
    return client.put("/api/irrigation/interval", json=body)


def test_without_an_interval_the_next_watering_is_not_configured(client):
    response = client.get("/api/irrigation/next")
    assert response.status_code == 200
    assert response.get_json()["configured"] is False


def test_setting_an_interval_shows_the_schedule_from_the_last_watering(client, temp_db):
    assert _put(client, {"days": 2}).status_code == 200
    temp_db.record_irrigation(_grow_id(temp_db), 5, [], date=_ago(2))

    body = client.get("/api/irrigation/next").get_json()
    assert body["configured"] is True
    assert body["interval_days"] == 2
    assert body["last_irrigation_date"] == _ago(2)
    assert body["next"]["state"] == "today"
    assert body["next"]["days_until"] == 0


def test_an_overdue_watering_is_reported_with_its_days(client, temp_db):
    _put(client, {"days": 2})
    temp_db.record_irrigation(_grow_id(temp_db), 5, [], date=_ago(5))

    next_ = client.get("/api/irrigation/next").get_json()["next"]
    assert next_["state"] == "overdue"
    assert next_["overdue_days"] == 3
    assert next_["due_date"] == _ago(3)


def test_a_configured_grow_with_no_waterings_is_due_today(client):
    _put(client, {"days": 4})
    body = client.get("/api/irrigation/next").get_json()
    assert body["configured"] is True
    assert body["last_irrigation_date"] is None
    assert body["next"]["state"] == "no_history"


def test_changing_the_interval_is_reflected_on_the_next_read(client, temp_db):
    _put(client, {"days": 2})
    temp_db.record_irrigation(_grow_id(temp_db), 5, [], date=_ago(2))
    _put(client, {"days": 6})

    body = client.get("/api/irrigation/next").get_json()
    assert body["interval_days"] == 6
    assert body["next"]["state"] == "upcoming"
    assert body["next"]["days_until"] == 4


def test_null_clears_the_interval(client):
    _put(client, {"days": 3})
    response = _put(client, {"days": None})
    assert response.status_code == 200
    assert response.get_json()["interval_days"] is None
    assert client.get("/api/irrigation/next").get_json()["configured"] is False


def test_the_interval_accepts_one_and_thirty(client):
    assert _put(client, {"days": 1}).status_code == 200
    assert _put(client, {"days": 30}).status_code == 200


def test_an_interval_outside_the_range_is_a_400(client):
    assert _put(client, {"days": 0}).status_code == 400
    assert _put(client, {"days": 31}).status_code == 400
    assert client.get("/api/irrigation/next").get_json()["configured"] is False


def test_only_whole_numbers_are_accepted(client):
    for bad in ["2", 2.5, True, [2]]:
        assert _put(client, {"days": bad}).status_code == 400, bad


def test_a_body_without_days_is_a_400(client):
    assert _put(client, {}).status_code == 400
    assert client.put("/api/irrigation/interval", json=None).status_code == 400
    assert client.put("/api/irrigation/interval", data="2", content_type="text/plain").status_code == 400
