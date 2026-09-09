from datetime import timedelta

from app.main import clock


def start_handoff(client, batch, *, destination="BENCH", ttl=10):
    container = batch["containers"][0]
    response = client.post(
        "/api/handoffs",
        json={
            "container_id": container["id"],
            "from_location_code": container["current_location"]["code"],
            "to_location_code": destination,
            "created_by": "alice",
            "ttl_minutes": ttl,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_confirm_moves_once_and_repeated_confirmation_is_idempotent(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    handoff = start_handoff(client, batch)

    first = client.post(
        "/api/handoffs/confirm", json={"code": handoff["receipt_code"], "received_by": "bob"}
    )
    second = client.post(
        "/api/handoffs/confirm", json={"code": handoff["receipt_code"], "received_by": "carol"}
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == "received"
    assert second.json()["replayed"] is True
    assert second.json()["received_by"] == "bob"

    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert detail["containers"][0]["current_location"]["code"] == "BENCH"
    received_events = [e for e in detail["timeline"] if e["event_type"] == "handoff_received"]
    assert len(received_events) == 1


def test_only_initiator_can_cancel_and_cancelled_code_cannot_move(client, batch_factory):
    handoff = start_handoff(client, batch_factory())
    denied = client.post(f"/api/handoffs/{handoff['id']}/cancel", json={"actor": "bob"})
    assert denied.status_code == 403
    cancelled = client.post(f"/api/handoffs/{handoff['id']}/cancel", json={"actor": "alice"})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    confirm = client.post(
        "/api/handoffs/confirm", json={"code": handoff["receipt_code"], "received_by": "bob"}
    )
    assert confirm.status_code == 409
    assert confirm.json()["error"]["code"] == "HANDOFF_CANCELLED"


def test_expiry_is_server_decided_persisted_as_anomaly_and_reopen_keeps_history(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    original = start_handoff(client, batch, ttl=1)
    mutable_clock.value += timedelta(seconds=60)

    expired = client.post(
        "/api/handoffs/confirm",
        json={"code": original["receipt_code"], "received_by": "late-receiver"},
    )
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "HANDOFF_EXPIRED"
    old = client.get(f"/api/handoffs/{original['id']}").json()
    assert old["persisted_status"] == "anomaly"

    blocked = client.post(
        "/api/handoffs",
        json={
            "container_id": batch["containers"][0]["id"],
            "from_location_code": "FRIDGE",
            "to_location_code": "WINDOW",
            "created_by": "alice",
            "ttl_minutes": 5,
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "UNRESOLVED_ANOMALY"

    reopened = client.post(f"/api/handoffs/{original['id']}/reopen", json={"actor": "dana"})
    assert reopened.status_code == 201
    assert reopened.json()["id"] != original["id"]
    assert len(reopened.json()["receipt_code"]) == 6
    old = client.get(f"/api/handoffs/{original['id']}").json()
    assert old["successor_id"] == reopened.json()["id"]
    assert old["resolution"] == "REOPENED"


def test_wrong_source_is_rejected_without_changing_container(client, batch_factory):
    batch = batch_factory()
    response = client.post(
        "/api/handoffs",
        json={
            "container_id": batch["containers"][0]["id"],
            "from_location_code": "BENCH",
            "to_location_code": "WINDOW",
            "created_by": "alice",
            "ttl_minutes": 5,
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LOCATION_MISMATCH"
    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert detail["containers"][0]["current_location"]["code"] == "FRIDGE"


def test_errors_have_stable_shape_and_trace_id(client):
    response = client.post(
        "/api/handoffs/confirm",
        headers={"X-Trace-ID": "acceptance-123"},
        json={"code": "123456", "received_by": "bob"},
    )
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "INVALID_RECEIPT_CODE",
            "message": "Receipt code was not found.",
            "retryable": False,
            "trace_id": "acceptance-123",
            "details": {},
        }
    }
