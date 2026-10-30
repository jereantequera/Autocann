"""
Alert state (#66): "Visto" and "Posponer" stored in the backend, per alert.

What is pinned here is the identity rule in store/alerts.py: an acknowledged
alert stays acknowledged while its condition lasts, comes back when something
new happens under it, and a different alert is never hidden by it.
"""

from __future__ import annotations

import pytest

from autocann.store.alerts import acknowledge_alert, snooze_alert, sync_alerts

NOW = 1_800_000_000
SCOPE = 1


def gap(last_at, first_at=None):
    return {"type": "data_gap", "severity": "warning", "message": "Hubo un hueco",
            "first_at": first_at or last_at, "last_at": last_at}


def spike(last_at):
    return {"type": "humidity_spike", "severity": "warning", "message": "Cambio brusco",
            "first_at": last_at, "last_at": last_at}


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_a_new_alert_is_unread_and_keeps_its_id_while_it_lasts(temp_db):
    first = sync_alerts([gap(NOW - 3600)], SCOPE, NOW)
    again = sync_alerts([gap(NOW - 3600)], SCOPE, NOW + 60)

    assert first[0]["unread"] is True
    assert again[0]["id"] == first[0]["id"]
    # The detector's own fields come through untouched.
    assert again[0]["message"] == "Hubo un hueco"


def test_an_acknowledged_alert_stays_seen_while_the_condition_lasts(temp_db):
    [item] = sync_alerts([gap(NOW - 3600)], SCOPE, NOW)
    assert acknowledge_alert(item["id"], NOW) is not None

    [after] = sync_alerts([gap(NOW - 3600)], SCOPE, NOW + 3600)

    assert after["unread"] is False
    assert after["acknowledged_at"] == NOW


def test_a_new_alert_shows_even_right_after_another_was_acknowledged(temp_db):
    [item] = sync_alerts([gap(NOW - 3600)], SCOPE, NOW)
    acknowledge_alert(item["id"], NOW)

    items = sync_alerts([gap(NOW - 3600), spike(NOW - 60)], SCOPE, NOW + 60)

    by_type = {i["type"]: i for i in items}
    assert by_type["data_gap"]["unread"] is False
    assert by_type["humidity_spike"]["unread"] is True


def test_a_new_occurrence_under_an_open_alert_makes_it_unread_again(temp_db):
    # A second gap in the night: the grower saw the first one, not this one.
    [item] = sync_alerts([gap(NOW - 3600)], SCOPE, NOW)
    acknowledge_alert(item["id"], NOW)

    [after] = sync_alerts([gap(NOW - 600, first_at=NOW - 3600)], SCOPE, NOW + 600)

    assert after["id"] == item["id"]
    assert after["unread"] is True


def test_resolved_and_recurring_is_a_new_unread_alert(temp_db):
    [item] = sync_alerts([gap(NOW - 3600)], SCOPE, NOW)
    acknowledge_alert(item["id"], NOW)

    assert sync_alerts([], SCOPE, NOW + 60) == []      # resolved
    [again] = sync_alerts([gap(NOW + 120)], SCOPE, NOW + 600)

    assert again["id"] != item["id"]
    assert again["unread"] is True


def test_hidden_for_a_while_with_the_same_occurrence_is_the_same_alert(temp_db):
    # A pause drops the gap from the report; on resume it is the same gap.
    [item] = sync_alerts([gap(NOW - 3600)], SCOPE, NOW)
    acknowledge_alert(item["id"], NOW)

    sync_alerts([], SCOPE, NOW + 60)
    [back] = sync_alerts([gap(NOW - 3600)], SCOPE, NOW + 600)

    assert back["id"] == item["id"]
    assert back["unread"] is False


def test_snooze_hides_until_the_time_passes(temp_db):
    [item] = sync_alerts([gap(NOW - 3600)], SCOPE, NOW)
    assert snooze_alert(item["id"], NOW + 3600) is not None

    [during] = sync_alerts([gap(NOW - 3600)], SCOPE, NOW + 1800)
    [after] = sync_alerts([gap(NOW - 3600)], SCOPE, NOW + 3601)

    assert during["unread"] is False
    assert after["unread"] is True


def test_alerts_are_scoped_per_grow(temp_db):
    [mine] = sync_alerts([gap(NOW - 3600)], 1, NOW)
    acknowledge_alert(mine["id"], NOW)

    [other] = sync_alerts([gap(NOW - 3600)], 2, NOW)

    assert other["id"] != mine["id"]
    assert other["unread"] is True
    # Syncing grow 2 did not resolve grow 1's alert.
    assert sync_alerts([gap(NOW - 3600)], 1, NOW + 60)[0]["id"] == mine["id"]


def test_a_resolved_alert_cannot_be_acknowledged(temp_db):
    [item] = sync_alerts([gap(NOW - 3600)], SCOPE, NOW)
    sync_alerts([], SCOPE, NOW + 60)

    assert acknowledge_alert(item["id"], NOW + 120) is None
    assert snooze_alert(item["id"], NOW + 3600) is None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def report(monkeypatch):
    """What the detector says; the test sets `.items` (warnings)."""
    import autocann.web.api.alerts as alerts_api

    class Report:
        items = []

    monkeypatch.setattr(alerts_api, "current_anomalies", lambda hours=24: {
        "status": "warning" if Report.items else "ok",
        "anomalies": [], "warnings": list(Report.items)})
    return Report


def test_api_acknowledging_on_one_device_hides_it_on_every_device(client, report):
    report.items = [gap(NOW - 3600)]
    body = client.get("/api/alerts").get_json()
    assert body["unread"] == 1
    alert_id = body["items"][0]["id"]

    assert client.post(f"/api/alerts/{alert_id}/ack").status_code == 200

    # Another browser asks: the state is the server's, not the page's.
    other = client.application.test_client().get("/api/alerts").get_json()
    assert other["unread"] == 0
    assert other["items"][0]["unread"] is False


def test_api_snooze_takes_minutes_and_refuses_nonsense(client, report):
    report.items = [gap(NOW - 3600)]
    alert_id = client.get("/api/alerts").get_json()["items"][0]["id"]

    for bad in ({}, {"minutes": 0}, {"minutes": "60"}, {"minutes": True}, {"minutes": 10**6}):
        response = client.post(f"/api/alerts/{alert_id}/snooze", json=bad)
        assert response.status_code == 400
        assert response.get_json()["code"] == "invalid_snooze"

    response = client.post(f"/api/alerts/{alert_id}/snooze", json={"minutes": 60})
    assert response.status_code == 200
    assert client.get("/api/alerts").get_json()["unread"] == 0


def test_api_unknown_alert_is_404_with_a_code(client, report):
    response = client.post("/api/alerts/999/ack")

    assert response.status_code == 404
    assert response.get_json()["code"] == "alert_not_found"


def test_an_existing_database_gets_the_table_without_losing_anything(temp_db):
    # The database on the Pi predates the table: schema setup must add it,
    # and leave the grows alone.
    from autocann.store import connection
    from autocann.store.schema import init_database

    conn = connection._open()
    conn.execute("DROP TABLE notifications")
    grows = conn.execute("SELECT COUNT(*) FROM grows").fetchone()[0]
    conn.commit()
    conn.close()

    init_database()

    conn = connection._open()
    assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM grows").fetchone()[0] == grows
    conn.close()
    assert sync_alerts([gap(NOW - 3600)], SCOPE, NOW)[0]["unread"] is True
