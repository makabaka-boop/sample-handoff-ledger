from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from app.main import clock

from .test_transitions import start_handoff


def replace(client, container_id, *, new_label="TUBE-B", actor="carol", reason="壳裂", note=None):
    body = {"new_label": new_label, "actor": actor, "reason": reason}
    if note is not None:
        body["note"] = note
    return client.post(f"/api/containers/{container_id}/replace", json=body)


def by_id(detail, container_id):
    return next(container for container in detail["containers"] if container["id"] == container_id)


def by_label(detail, label):
    return next(container for container in detail["containers"] if container["label"] == label)


def test_replace_seals_old_container_and_successor_takes_over_identity(client, batch_factory):
    batch = batch_factory()
    old_id = batch["containers"][0]["id"]

    response = replace(client, old_id, note="外壳碎裂，已转装")
    assert response.status_code == 200, response.text
    detail = response.json()

    old = by_id(detail, old_id)
    assert old["status"] == "replaced"
    assert old["replaced_by"] == "carol"
    assert old["replaced_at"] is not None
    assert old["replacement_reason"] == "壳裂"

    new = by_label(detail, "TUBE-B")
    assert new["status"] == "active"
    assert old["replacement_container_id"] == new["id"]
    assert new["replacement_container_id"] is None
    assert new["replaced_by"] is None
    # The successor stands in the same place with the same exposure clock.
    assert new["current_location"]["code"] == old["current_location"]["code"] == "FRIDGE"
    assert new["accumulated_out_seconds"] == old["accumulated_out_seconds"] == 0
    assert new["out_since"] is None

    events = [e for e in detail["timeline"] if e["event_type"] == "container_replaced"]
    assert len(events) == 1
    event = events[0]
    assert event["container_id"] == old_id
    assert event["actor"] == "carol"
    assert event["note"] == "外壳碎裂，已转装"
    assert event["details"]["old_label"] == "TUBE-A"
    assert event["details"]["new_label"] == "TUBE-B"
    assert event["details"]["new_container_id"] == new["id"]
    assert event["details"]["reason"] == "壳裂"


def test_exposure_timing_is_continuous_across_replacement_when_out_of_storage(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory(location="BENCH", max_out=30)
    old_id = batch["containers"][0]["id"]
    # The container has been on the warm bench for 90 seconds before damage.
    mutable_clock.value += timedelta(seconds=90)

    detail = replace(client, old_id).json()
    old = by_id(detail, old_id)
    new = by_label(detail, "TUBE-B")
    assert new["accumulated_out_seconds"] == 0
    assert new["total_out_seconds"] == 90
    assert new["out_since"] == old["out_since"]

    # Time keeps accruing against the same out_since after transloading.
    mutable_clock.value += timedelta(seconds=30)
    detail = client.get(f"/api/batches/{batch['id']}").json()
    new = by_label(detail, "TUBE-B")
    assert new["current_out_seconds"] == 120
    assert new["total_out_seconds"] == 120
    assert by_id(detail, old_id)["total_out_seconds"] == 120


def test_new_container_can_start_handoffs_and_old_container_is_operational_dead_end(
    client, batch_factory
):
    batch = batch_factory()
    old_id = batch["containers"][0]["id"]
    detail = replace(client, old_id).json()
    new_id = by_label(detail, "TUBE-B")["id"]

    handoff = client.post(
        "/api/handoffs",
        json={
            "container_id": new_id,
            "from_location_code": "FRIDGE",
            "to_location_code": "BENCH",
            "created_by": "alice",
            "ttl_minutes": 10,
        },
    )
    assert handoff.status_code == 201, handoff.text
    assert handoff.json()["container_id"] == new_id
    assert handoff.json()["container_label"] == "TUBE-B"

    blocked = client.post(
        "/api/handoffs",
        json={
            "container_id": old_id,
            "from_location_code": "FRIDGE",
            "to_location_code": "BENCH",
            "created_by": "alice",
            "ttl_minutes": 10,
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "CONTAINER_ALREADY_REPLACED"
    assert blocked.json()["error"]["details"]["replacement_container_id"] == new_id

    again = replace(client, old_id, new_label="TUBE-C")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "CONTAINER_ALREADY_REPLACED"


def test_replacement_is_blocked_while_a_handoff_is_pending(client, batch_factory):
    batch = batch_factory()
    old_id = batch["containers"][0]["id"]
    start_handoff(client, batch)

    blocked = replace(client, old_id)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "HANDOFF_ALREADY_PENDING"

    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert len(detail["containers"]) == 1
    assert detail["containers"][0]["status"] == "active"
    assert [e for e in detail["timeline"] if e["event_type"] == "container_replaced"] == []


def test_duplicate_label_rolls_back_without_sealing_or_timeline(client, batch_factory):
    batch = batch_factory()
    old_id = batch["containers"][0]["id"]

    conflict = replace(client, old_id, new_label="TUBE-A")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "CONTAINER_LABEL_EXISTS"

    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert len(detail["containers"]) == 1
    old = by_id(detail, old_id)
    assert old["status"] == "active"
    assert old["label"] == "TUBE-A"
    assert old["replacement_container_id"] is None
    assert old["replaced_at"] is None
    assert [e for e in detail["timeline"] if e["event_type"] == "container_replaced"] == []


def test_label_conflict_is_case_insensitive(client, batch_factory):
    batch = batch_factory()
    response = replace(client, batch["containers"][0]["id"], new_label="tube-a")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONTAINER_LABEL_EXISTS"


def test_replacement_requires_active_batch_and_rolls_back(client, batch_factory):
    from .test_rejections import reject

    batch = batch_factory()
    old_id = batch["containers"][0]["id"]
    handoff = start_handoff(client, batch)
    rejected = reject(client, handoff["receipt_code"], reason="seal_broken")
    assert rejected.status_code == 200
    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert detail["disposition"] == "review"

    blocked = replace(client, old_id)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "BATCH_NOT_ACTIVE"
    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert len(detail["containers"]) == 1
    assert detail["containers"][0]["status"] == "active"


def test_replacement_chain_preserves_history_and_historical_handoffs_still_read(
    client, batch_factory
):
    batch = batch_factory()
    first_id = batch["containers"][0]["id"]

    # First container ships out and is received at the bench before cracking.
    handoff = start_handoff(client, batch, destination="BENCH")
    client.post(
        "/api/handoffs/confirm",
        json={"code": handoff["receipt_code"], "received_by": "bob"},
    )

    detail = replace(client, first_id, new_label="TUBE-B", reason="封签破损").json()
    second_id = by_label(detail, "TUBE-B")["id"]
    detail = replace(client, second_id, new_label="TUBE-C", actor="dana", reason="再次转装").json()
    third_id = by_label(detail, "TUBE-C")["id"]

    first = by_id(detail, first_id)
    second = by_id(detail, second_id)
    third = by_id(detail, third_id)
    assert first["status"] == second["status"] == "replaced"
    assert third["status"] == "active"
    assert first["replacement_container_id"] == second_id
    assert second["replacement_container_id"] == third_id
    assert third["replacement_container_id"] is None
    assert third["current_location"]["code"] == "BENCH"

    replaced_events = [e for e in detail["timeline"] if e["event_type"] == "container_replaced"]
    assert [event["actor"] for event in replaced_events] == ["dana", "carol"]
    assert replaced_events[0]["details"]["old_label"] == "TUBE-B"
    assert replaced_events[1]["details"]["old_label"] == "TUBE-A"

    # Historical handoffs keep pointing at the original container and remain readable.
    historical = client.get(f"/api/handoffs/{handoff['id']}").json()
    assert historical["container_id"] == first_id
    assert historical["container_label"] == "TUBE-A"
    assert historical["status"] == "received"

    # The active successor carries the chain forward.
    onward = client.post(
        "/api/handoffs",
        json={
            "container_id": third_id,
            "from_location_code": "BENCH",
            "to_location_code": "WINDOW",
            "created_by": "alice",
            "ttl_minutes": 10,
        },
    )
    assert onward.status_code == 201, onward.text
    assert onward.json()["container_id"] == third_id


def test_concurrent_replacement_has_one_winner_and_no_partial_state(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    old_id = batch["containers"][0]["id"]

    def worker(label):
        return label, replace(client, old_id, new_label=label)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(worker, ["TUBE-B", "TUBE-C"]))

    statuses = sorted(response.status_code for _, response in outcomes)
    assert statuses == [200, 409]
    loser = next(response for _, response in outcomes if response.status_code == 409)
    assert loser.json()["error"]["code"] == "CONTAINER_ALREADY_REPLACED"

    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert len(detail["containers"]) == 2
    replaced = [c for c in detail["containers"] if c["status"] == "replaced"]
    active = [c for c in detail["containers"] if c["status"] == "active"]
    assert len(replaced) == 1 and len(active) == 1
    assert replaced[0]["replacement_container_id"] == active[0]["id"]
    assert (
        len([e for e in detail["timeline"] if e["event_type"] == "container_replaced"]) == 1
    )


def test_blank_note_is_normalised_to_none(client, batch_factory):
    batch = batch_factory()
    detail = replace(client, batch["containers"][0]["id"], note="   ").json()
    event = next(e for e in detail["timeline"] if e["event_type"] == "container_replaced")
    assert event["note"] is None
