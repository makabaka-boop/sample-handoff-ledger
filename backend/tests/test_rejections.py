from datetime import timedelta

from app.main import clock

from .test_transitions import start_handoff


def reject(client, code, *, by="night-receiver", reason="seal_broken", note=None):
    body = {"code": code, "rejected_by": by, "reason": reason}
    if note is not None:
        body["note"] = note
    return client.post("/api/handoffs/reject", json=body)


def test_receiver_rejection_marks_anomaly_and_batch_review_without_moving(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    handoff = start_handoff(client, batch)

    response = reject(client, handoff["receipt_code"], note="封签撕毁，无法核验")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "anomaly"
    assert body["persisted_status"] == "anomaly"
    assert body["anomaly_reason"] == "RECEIVER_REJECTED"
    assert body["rejected_by"] == "night-receiver"
    assert body["reject_reason"] == "seal_broken"
    assert body["reject_note"] == "封签撕毁，无法核验"
    assert body["replayed"] is False
    assert body["rejected_at"] is not None

    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert detail["containers"][0]["current_location"]["code"] == "FRIDGE"
    assert detail["disposition"] == "review"
    events = detail["timeline"]
    rejected = [e for e in events if e["event_type"] == "handoff_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["details"]["reason"] == "seal_broken"
    assert rejected[0]["note"] == "封签撕毁，无法核验"
    assert rejected[0]["actor"] == "night-receiver"


def test_repeated_rejection_returns_first_fact_and_marks_replayed(client, batch_factory):
    handoff = start_handoff(client, batch_factory())
    first = reject(client, handoff["receipt_code"], by="bob", reason="label_mismatch")
    second = reject(client, handoff["receipt_code"], by="carol", reason="other", note="second")
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert second.json()["rejected_by"] == "bob"
    assert second.json()["reject_reason"] == "label_mismatch"
    assert second.json()["reject_note"] is None

    detail = client.get(f"/api/batches/{handoff['batch_id']}").json()
    assert len([e for e in detail["timeline"] if e["event_type"] == "handoff_rejected"]) == 1


def test_confirmation_after_rejection_returns_handoff_rejected(client, batch_factory):
    handoff = start_handoff(client, batch_factory())
    rejected = reject(client, handoff["receipt_code"])
    assert rejected.status_code == 200
    denied = client.post(
        "/api/handoffs/confirm",
        json={"code": handoff["receipt_code"], "received_by": "late-bob"},
    )
    assert denied.status_code == 409
    error = denied.json()["error"]
    assert error["code"] == "HANDOFF_REJECTED"
    assert error["retryable"] is False
    assert set(error) == {"code", "message", "retryable", "trace_id", "details"}


def test_rejection_after_confirmation_returns_handoff_already_received(
    client, batch_factory
):
    handoff = start_handoff(client, batch_factory())
    confirmed = client.post(
        "/api/handoffs/confirm",
        json={"code": handoff["receipt_code"], "received_by": "bob"},
    )
    assert confirmed.status_code == 200
    denied = reject(client, handoff["receipt_code"])
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "HANDOFF_ALREADY_RECEIVED"

    detail = client.get(f"/api/batches/{handoff['batch_id']}").json()
    assert detail["containers"][0]["current_location"]["code"] == "BENCH"
    assert len([e for e in detail["timeline"] if e["event_type"] == "handoff_rejected"]) == 0


def test_rejection_of_expired_code_uses_unified_expired_error_and_no_partial_write(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    handoff = start_handoff(client, batch, ttl=1)
    mutable_clock.value += timedelta(seconds=60)

    response = reject(client, handoff["receipt_code"])
    assert response.status_code == 410
    error = response.json()["error"]
    assert error["code"] == "HANDOFF_EXPIRED"
    assert set(error) == {"code", "message", "retryable", "trace_id", "details"}

    persisted = client.get(f"/api/handoffs/{handoff['id']}").json()
    assert persisted["persisted_status"] == "anomaly"
    assert persisted["anomaly_reason"] == "HANDOFF_EXPIRED"
    assert persisted["rejected_by"] is None
    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert detail["disposition"] == "active"
    assert detail["containers"][0]["current_location"]["code"] == "FRIDGE"
    assert len([e for e in detail["timeline"] if e["event_type"] == "handoff_rejected"]) == 0


def test_rejection_of_cancelled_code_uses_unified_cancelled_error(client, batch_factory):
    handoff = start_handoff(client, batch_factory())
    cancelled = client.post(
        f"/api/handoffs/{handoff['id']}/cancel", json={"actor": "alice"}
    )
    assert cancelled.status_code == 200
    response = reject(client, handoff["receipt_code"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "HANDOFF_CANCELLED"


def test_rejection_of_unknown_code_uses_unified_not_found_error(client):
    response = client.post(
        "/api/handoffs/reject",
        json={"code": "000000", "rejected_by": "bob", "reason": "other"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INVALID_RECEIPT_CODE"


def test_rejection_rejects_unknown_reason_without_writes(client, batch_factory):
    handoff = start_handoff(client, batch_factory())
    response = client.post(
        "/api/handoffs/reject",
        json={
            "code": handoff["receipt_code"],
            "rejected_by": "bob",
            "reason": "not_a_reason",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    persisted = client.get(f"/api/handoffs/{handoff['id']}").json()
    assert persisted["persisted_status"] == "pending"


def test_cold_to_warm_rejection_settles_exposure_and_clears_out_since(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory(location="FRIDGE", max_out=30)
    handoff = start_handoff(client, batch, destination="BENCH")
    mutable_clock.value += timedelta(seconds=90)

    response = reject(client, handoff["receipt_code"], reason="package_contaminated")
    assert response.status_code == 200
    detail = client.get(f"/api/batches/{batch['id']}").json()
    container = detail["containers"][0]
    assert container["current_location"]["code"] == "FRIDGE"
    assert container["accumulated_out_seconds"] == 90
    assert container["out_since"] is None
    assert container["current_out_seconds"] == 0


def test_non_cold_source_rejection_does_not_touch_exposure_timer(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory(location="BENCH", max_out=30)
    handoff = start_handoff(client, batch, destination="WINDOW")
    mutable_clock.value += timedelta(seconds=90)

    response = reject(client, handoff["receipt_code"], reason="other")
    assert response.status_code == 200
    detail = client.get(f"/api/batches/{batch['id']}").json()
    container = detail["containers"][0]
    assert container["current_location"]["code"] == "BENCH"
    assert container["out_since"] is not None
    assert container["current_out_seconds"] == 90


def test_rejection_without_note_is_accepted_and_rejected_anomaly_blocks_new_handoff(
    client, batch_factory
):
    batch = batch_factory()
    handoff = start_handoff(client, batch)
    response = reject(client, handoff["receipt_code"], reason="label_mismatch")
    assert response.status_code == 200
    assert response.json()["reject_note"] is None

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


def test_rejected_handoff_can_reopen_and_issue_a_new_code(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    handoff = start_handoff(client, batch)
    reject(client, handoff["receipt_code"], reason="seal_broken")

    reopened = client.post(
        f"/api/handoffs/{handoff['id']}/reopen", json={"actor": "dana"}
    )
    assert reopened.status_code == 201, reopened.text
    assert reopened.json()["id"] != handoff["id"]
    assert len(reopened.json()["receipt_code"]) == 6
    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert detail["disposition"] == "active"
    assert len([e for e in detail["timeline"] if e["event_type"] == "handoff_rejected"]) == 1
