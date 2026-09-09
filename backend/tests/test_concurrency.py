from concurrent.futures import ThreadPoolExecutor

from app.main import clock

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
