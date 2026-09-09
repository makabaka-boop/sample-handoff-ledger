from concurrent.futures import ThreadPoolExecutor

from app.main import clock

from .test_rejections import reject
from .test_transitions import start_handoff


def test_two_receivers_with_same_code_observe_one_move(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    handoff = start_handoff(client, batch)

    def receive(actor):
        return client.post(
            "/api/handoffs/confirm",
            json={"code": handoff["receipt_code"], "received_by": actor},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(receive, ["bob", "carol"]))
    assert first.status_code == second.status_code == 200
    assert sorted([first.json()["replayed"], second.json()["replayed"]]) == [False, True]
    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert detail["containers"][0]["current_location"]["code"] == "BENCH"
    assert len([e for e in detail["timeline"] if e["event_type"] == "handoff_received"]) == 1


def test_two_rejections_with_same_code_observe_one_rejection_event(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    handoff = start_handoff(client, batch)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(
            executor.map(
                lambda actor: reject(client, handoff["receipt_code"], by=actor),
                ["night-a", "night-b"],
            )
        )
    assert first.status_code == second.status_code == 200
    assert sorted([first.json()["replayed"], second.json()["replayed"]]) == [False, True]
    winner = first.json() if not first.json()["replayed"] else second.json()
    loser = second.json() if first.json() is not winner else first.json()
    assert winner["rejected_by"] == loser["rejected_by"]
    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert detail["containers"][0]["current_location"]["code"] == "FRIDGE"
    assert detail["disposition"] == "review"
    assert len([e for e in detail["timeline"] if e["event_type"] == "handoff_rejected"]) == 1


def test_confirm_and_reject_compete_with_only_one_winner(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    handoff = start_handoff(client, batch)

    outcomes = []

    def worker(kind):
        if kind == "confirm":
            return kind, client.post(
                "/api/handoffs/confirm",
                json={"code": handoff["receipt_code"], "received_by": "bob"},
            )
        return kind, reject(
            client, handoff["receipt_code"], by="night-a", reason="seal_broken"
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        for kind, response in executor.map(worker, ["confirm", "reject"]):
            outcomes.append((kind, response))

    confirm_response = next(response for kind, response in outcomes if kind == "confirm")
    reject_response = next(response for kind, response in outcomes if kind == "reject")
    if confirm_response.status_code == 200:
        assert confirm_response.json()["status"] == "received"
        assert reject_response.status_code == 409
        assert reject_response.json()["error"]["code"] == "HANDOFF_ALREADY_RECEIVED"
    else:
        assert reject_response.status_code == 200
        assert reject_response.json()["status"] == "anomaly"
        assert confirm_response.status_code == 409
        assert confirm_response.json()["error"]["code"] == "HANDOFF_REJECTED"

    detail = client.get(f"/api/batches/{batch['id']}").json()
    received = [e for e in detail["timeline"] if e["event_type"] == "handoff_received"]
    rejected = [e for e in detail["timeline"] if e["event_type"] == "handoff_rejected"]
    assert len(received) + len(rejected) == 1
