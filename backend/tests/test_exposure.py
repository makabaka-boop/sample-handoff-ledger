from datetime import timedelta

from app.main import clock

from .test_transitions import start_handoff


def test_exposure_below_boundary_allows_return_to_cold_storage(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory(location="BENCH", max_out=1)
    handoff = start_handoff(client, batch, destination="FRIDGE")
    mutable_clock.value += timedelta(seconds=59)
    response = client.post(
        "/api/handoffs/confirm", json={"code": handoff["receipt_code"], "received_by": "bob"}
    )
    assert response.status_code == 200
    detail = client.get(f"/api/batches/{batch['id']}").json()
    container = detail["containers"][0]
    assert container["current_location"]["code"] == "FRIDGE"
    assert container["total_out_seconds"] == 59


def test_exact_exposure_boundary_becomes_anomaly_and_does_not_move(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory(location="BENCH", max_out=1)
    handoff = start_handoff(client, batch, destination="FRIDGE")
    mutable_clock.value += timedelta(seconds=60)
    response = client.post(
        "/api/handoffs/confirm", json={"code": handoff["receipt_code"], "received_by": "bob"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EXPOSURE_LIMIT_EXCEEDED"
    persisted = client.get(f"/api/handoffs/{handoff['id']}").json()
    assert persisted["status"] == "anomaly"
    assert persisted["anomaly_reason"] == "EXPOSURE_LIMIT_EXCEEDED"
    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert detail["containers"][0]["current_location"]["code"] == "BENCH"


def test_receiver_delay_after_cold_storage_removal_counts_as_exposure(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory(location="FRIDGE", max_out=1)
    handoff = start_handoff(client, batch, destination="BENCH")
    mutable_clock.value += timedelta(seconds=60)

    response = client.post(
        "/api/handoffs/confirm",
        json={"code": handoff["receipt_code"], "received_by": "late-receiver"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EXPOSURE_LIMIT_EXCEEDED"
    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert detail["containers"][0]["total_out_seconds"] == 60
    assert detail["containers"][0]["current_location"]["code"] == "FRIDGE"
