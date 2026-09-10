from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import clock
from app.models import LocationInventoryCheck, LocationInventoryCheckItem


def location_id(client, code):
    locations = {item["code"]: item["id"] for item in client.get("/api/locations").json()}
    return locations[code]


def submit_check(client, location_id_value, *, labels, by="night-a"):
    return client.post(
        f"/api/locations/{location_id_value}/inventory-checks",
        json={"checked_by": by, "labels": labels},
    )


def make_batch(client, accession, labels, *, location="FRIDGE"):
    response = client.post(
        "/api/batches",
        json={
            "accession_number": accession,
            "temp_min_c": 2,
            "temp_max_c": 8,
            "max_out_minutes": 30,
            "created_by": "alice",
            "containers": [
                {"label": label, "initial_location_code": location} for label in labels
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def move_container(client, batch, container, destination="BENCH", receiver="bob"):
    handoff = client.post(
        "/api/handoffs",
        json={
            "container_id": container["id"],
            "from_location_code": container["current_location"]["code"],
            "to_location_code": destination,
            "created_by": "alice",
            "ttl_minutes": 10,
        },
    )
    assert handoff.status_code == 201, handoff.text
    code = handoff.json()["receipt_code"]
    confirmed = client.post(
        "/api/handoffs/confirm", json={"code": code, "received_by": receiver}
    )
    assert confirmed.status_code == 200, confirmed.text
    return batch


def item_map(result):
    return {item["category"]: item for item in result["items"]}


def all_check_rows():
    with SessionLocal() as db:
        checks = db.scalar(select(func.count()).select_from(LocationInventoryCheck))
        items = db.scalar(select(func.count()).select_from(LocationInventoryCheckItem))
        return checks, items


def test_full_match_records_one_snapshot_with_submitter_and_server_time(
    client, batch_factory
):
    batch = make_batch(client, "INV-FULL", ["TUBE-A", "TUBE-B"])
    before = datetime.now(UTC)
    response = submit_check(client, location_id(client, "FRIDGE"), labels=[" tube-a ", "TUBE-B"])
    after = datetime.now(UTC)
    assert response.status_code == 201, response.text
    result = response.json()

    assert result["matched_count"] == 2
    assert result["missing_count"] == 0
    assert result["misplaced_count"] == 0
    assert result["unknown_count"] == 0
    assert result["scanned_count"] == 2
    assert result["checked_by"] == "night-a"
    assert result["location"]["code"] == "FRIDGE"
    created_at = datetime.fromisoformat(result["created_at"])
    assert before <= created_at <= after
    categories = {item["category"] for item in result["items"]}
    assert categories == {"matched"}
    assert {item["scanned_label"] for item in result["items"]} == {"tube-a", "TUBE-B"}
    for item in result["items"]:
        assert item["recorded_location_code"] == "FRIDGE"
        assert item["accession_number"] == "INV-FULL"
    # Exactly one immutable check with exactly its own two detail rows.
    assert all_check_rows() == (1, 2)

    # Readback through both endpoints stays identical.
    detail = client.get(f"/api/inventory-checks/{result['id']}").json()
    assert detail["matched_count"] == 2
    listing = client.get(
        f"/api/locations/{location_id(client, 'FRIDGE')}/inventory-checks"
    ).json()
    assert [row["id"] for row in listing] == [result["id"]]
    # The batch itself is untouched by the evidence record.
    refreshed = client.get(f"/api/batches/{batch['id']}").json()
    assert refreshed["disposition"] == "active"
    assert [c["current_location"]["code"] for c in refreshed["containers"]] == [
        "FRIDGE",
        "FRIDGE",
    ]


def test_mixed_differences_are_classified_and_misplaced_carries_recorded_location(client):
    make_batch(client, "INV-MIX-1", ["A1", "A2"])
    other = make_batch(client, "INV-MIX-2", ["B1"])
    move_container(client, other, other["containers"][0])

    response = submit_check(
        client,
        location_id(client, "FRIDGE"),
        labels=["A1", "B1", "GHOST-9"],
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert (
        result["matched_count"],
        result["missing_count"],
        result["misplaced_count"],
        result["unknown_count"],
    ) == (1, 1, 1, 1)

    groups = {}
    for item in result["items"]:
        groups.setdefault(item["category"], []).append(item)
    assert [i["scanned_label"] for i in groups["matched"]] == ["A1"]
    assert [i["container_label"] for i in groups["missing"]] == ["A2"]
    misplaced = groups["misplaced"][0]
    assert misplaced["scanned_label"] == "B1"
    # The system-recorded location lets staff correct this via a normal handoff.
    assert misplaced["recorded_location_code"] == "BENCH"
    assert misplaced["recorded_location_name"] == "处理台"
    assert misplaced["accession_number"] == "INV-MIX-2"
    unknown = groups["unknown"][0]
    assert unknown["scanned_label"] == "GHOST-9"
    assert unknown["container_id"] is None
    assert unknown["recorded_location_code"] is None
    # Items are grouped by category in business order for the UI.
    categories = [item["category"] for item in result["items"]]
    assert categories == ["matched", "missing", "misplaced", "unknown"]


def test_check_only_forms_evidence_positions_disposition_and_timeline_unchanged(client):
    first = make_batch(client, "INV-EVIDENCE-1", ["A1", "A2"])
    second = make_batch(client, "INV-EVIDENCE-2", ["B1"])
    move_container(client, second, second["containers"][0])

    response = submit_check(
        client,
        location_id(client, "FRIDGE"),
        labels=["A1", "B1", "GHOST"],
    )
    assert response.status_code == 201, response.text

    batch_one = client.get(f"/api/batches/{first['id']}").json()
    batch_two = client.get(f"/api/batches/{second['id']}").json()
    # No batch moved or changed disposition.
    assert batch_one["disposition"] == "active"
    assert batch_two["disposition"] == "active"
    assert [c["current_location"]["code"] for c in batch_one["containers"]] == [
        "FRIDGE",
        "FRIDGE",
    ]
    assert batch_two["containers"][0]["current_location"]["code"] == "BENCH"
    # Inventory writes no responsibility-chain events on either batch.
    types = {event["event_type"] for event in batch_one["timeline"]}
    assert types == {"batch_created", "container_registered"}
    types_two = {event["event_type"] for event in batch_two["timeline"]}
    assert types_two == {
        "batch_created",
        "container_registered",
        "handoff_initiated",
        "handoff_received",
    }


def test_duplicate_or_blank_labels_return_invalid_code_and_nothing_is_persisted(client):
    make_batch(client, "INV-INVALID", ["A1"])
    fridge = location_id(client, "FRIDGE")

    for labels in (
        ["A1", "a1"],          # duplicate ignoring case and surrounding space
        ["A1", " A1 "],
        ["", "A1"],            # blank entry
        ["   "],
    ):
        response = submit_check(client, fridge, labels=labels)
        assert response.status_code == 422, labels
        error = response.json()["error"]
        assert error["code"] == "INVALID_INVENTORY_LABELS"
    # An empty list fails request validation and also persists nothing.
    response = client.post(
        f"/api/locations/{fridge}/inventory-checks",
        json={"checked_by": "night-a", "labels": []},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert all_check_rows() == (0, 0)


def test_unknown_location_returns_location_not_found(client):
    response = submit_check(client, "does-not-exist", labels=["A1"])
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "LOCATION_NOT_FOUND"
    assert all_check_rows() == (0, 0)


def test_non_cold_locations_cannot_be_inventoried(client):
    make_batch(client, "INV-COLD-ONLY", ["A1"])
    response = submit_check(client, location_id(client, "BENCH"), labels=["A1"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LOCATION_NOT_COLD_STORAGE"
    # Even though a container is actually at BENCH mid-handoff, no check exists.
    listing = client.get(
        f"/api/locations/{location_id(client, 'BENCH')}/inventory-checks"
    ).json()
    assert listing == []


def test_failed_detail_write_rolls_back_the_check_header_as_well(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    make_batch(client, "INV-ROLLBACK", ["A1", "A2"])

    from sqlalchemy.orm import Session

    real_add = Session.add
    adds = {"count": 0}

    def add_then_fail_for_items(self, instance):  # type: ignore[no-untyped-def]
        adds["count"] += 1
        if adds["count"] >= 2:
            # The header (first add) is flushed before the detail rows; failing
            # here must roll the header insert back as well.
            raise RuntimeError("snapshot store unavailable")
        return real_add(self, instance)

    monkeypatch.setattr(Session, "add", add_then_fail_for_items)

    with pytest.raises(RuntimeError, match="snapshot store unavailable"):
        submit_check(client, location_id(client, "FRIDGE"), labels=["A1", "A2"])

    # The transaction rolled back: neither header nor detail rows survive.
    assert all_check_rows() == (0, 0)

    # The containers themselves were not affected; a clean retry then succeeds.
    monkeypatch.undo()
    response = submit_check(client, location_id(client, "FRIDGE"), labels=["A1", "A2"])
    assert response.status_code == 201, response.text
    assert response.json()["matched_count"] == 2
    assert all_check_rows() == (1, 2)


def test_checks_are_immutable_and_recent_history_is_newest_first(client):
    make_batch(client, "INV-HISTORY", ["A1"])
    fridge = location_id(client, "FRIDGE")

    first = submit_check(client, fridge, labels=["A1"], by="night-a")
    assert first.status_code == 201
    first_data = first.json()
    assert first_data["recent_checks"] == []

    second = submit_check(client, fridge, labels=["A1", "GHOST"], by="night-b")
    assert second.status_code == 201
    second_data = second.json()
    assert [row["id"] for row in second_data["recent_checks"]] == [first_data["id"]]
    assert second_data["recent_checks"][0]["checked_by"] == "night-a"

    # The first record is immutable and still excludes later checks.
    reread = client.get(f"/api/inventory-checks/{first_data['id']}").json()
    assert reread["matched_count"] == 1
    assert reread["unknown_count"] == 0
    assert [row["id"] for row in reread["recent_checks"]] == []

    listing = client.get(f"/api/locations/{fridge}/inventory-checks").json()
    assert [row["id"] for row in listing] == [second_data["id"], first_data["id"]]


def test_replaced_containers_drop_out_of_the_circulating_snapshot(client):
    batch = make_batch(client, "INV-REPLACED", ["CRACKED"])
    old_id = batch["containers"][0]["id"]
    sealed = client.post(
        f"/api/containers/{old_id}/replace",
        json={"new_label": "SOUND", "actor": "carol", "reason": "壳裂"},
    )
    assert sealed.status_code == 200, sealed.text

    result = submit_check(
        client,
        location_id(client, "FRIDGE"),
        labels=["SOUND", "CRACKED"],
    )
    assert result.status_code == 201, result.text
    data = result.json()
    # The successor is circulating and matches; the sealed label is no longer
    # part of the book snapshot, so scanning it reads as unknown.
    assert data["matched_count"] == 1
    assert data["missing_count"] == 0
    assert data["unknown_count"] == 1
    groups = item_map(data)
    assert groups["matched"]["container_label"] == "SOUND"
    assert groups["unknown"]["scanned_label"] == "CRACKED"


def test_pending_handoff_leaves_container_in_fridge_snapshot_until_confirmation(
    client,
):
    from .test_transitions import start_handoff

    batch = make_batch(client, "INV-PENDING", ["A1"])
    start_handoff(client, batch, destination="BENCH")

    # Initiation sets out_since but the container is still recorded in FRIDGE.
    result = submit_check(client, location_id(client, "FRIDGE"), labels=["A1"])
    assert result.status_code == 201, result.text
    assert result.json()["matched_count"] == 1
    assert all_check_rows() == (1, 1)


def test_scanning_one_of_two_same_label_containers_at_location_marks_other_missing(
    client,
):
    # Duplicate labels are allowed across batches (only forbidden within one).
    first = make_batch(client, "INV-DUP-LABEL-1", ["SAME-LABEL"])
    second = make_batch(client, "INV-DUP-LABEL-2", ["SAME-LABEL"])
    assert [c["current_location"]["code"] for c in first["containers"]] == ["FRIDGE"]
    assert [c["current_location"]["code"] for c in second["containers"]] == ["FRIDGE"]

    response = submit_check(client, location_id(client, "FRIDGE"), labels=["SAME-LABEL"])
    assert response.status_code == 201, response.text
    data = response.json()

    # One scanned label resolves to exactly one book container (matched); the
    # other same-label container was physically present on the book but never
    # scanned, so it must count as a book-missing row.
    assert data["matched_count"] == 1
    assert data["missing_count"] == 1
    assert data["misplaced_count"] == 0
    assert data["unknown_count"] == 0

    matched_ids = [
        item["container_id"]
        for item in data["items"]
        if item["category"] == "matched"
    ]
    missing = [item for item in data["items"] if item["category"] == "missing"]
    assert len(matched_ids) == 1
    assert len(missing) == 1
    assert missing[0]["container_label"] == "SAME-LABEL"
    assert missing[0]["recorded_location_code"] == "FRIDGE"
    book_ids = {first["containers"][0]["id"], second["containers"][0]["id"]}
    assert set(matched_ids) | {missing[0]["container_id"]} == book_ids
    assert set(matched_ids) & {missing[0]["container_id"]} == set()

    # Exactly two detail rows: one matched, one missing.
    assert all_check_rows() == (1, 2)


def test_second_submission_at_frozen_server_time_carries_the_first_check_as_recent(
    client,
    mutable_clock,
    monkeypatch,
):
    # A fixed server-side time (e.g. a frozen clock) must not make the second
    # recount believe it has no predecessor.
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    make_batch(client, "INV-FROZEN", ["A1", "A2"])
    fridge = location_id(client, "FRIDGE")

    first = submit_check(client, fridge, labels=["A1"], by="night-a")
    assert first.status_code == 201, first.text
    first_data = first.json()
    assert first_data["recent_checks"] == []

    second = submit_check(client, fridge, labels=["A1", "A2"], by="night-a")
    assert second.status_code == 201, second.text
    second_data = second.json()
    assert second_data["created_at"] == first_data["created_at"]
    assert [row["id"] for row in second_data["recent_checks"]] == [first_data["id"]]

    # Submitting a third time without advancing the clock still chains correctly.
    third = submit_check(client, fridge, labels=["A2"], by="night-b")
    assert third.status_code == 201, third.text
    third_data = third.json()
    assert [row["id"] for row in third_data["recent_checks"]] == [
        second_data["id"],
        first_data["id"],
    ]

    # The earlier immutable checks never gain later records when re-read.
    first_reread = client.get(f"/api/inventory-checks/{first_data['id']}").json()
    assert first_reread["recent_checks"] == []
    second_reread = client.get(f"/api/inventory-checks/{second_data['id']}").json()
    assert [row["id"] for row in second_reread["recent_checks"]] == [first_data["id"]]

    # The listing orders by submission sequence too.
    listing = client.get(f"/api/locations/{fridge}/inventory-checks").json()
    assert [row["id"] for row in listing] == [
        third_data["id"],
        second_data["id"],
        first_data["id"],
    ]


def test_blank_checked_by_is_rejected_before_transaction_and_nothing_is_persisted(
    client,
):
    make_batch(client, "INV-BLANK-BY", ["A1"])
    fridge = location_id(client, "FRIDGE")

    for checker in ("   ", "\t", " \n "):
        response = submit_check(client, fridge, labels=["A1"], by=checker)
        assert response.status_code == 422, checker
        assert response.json()["error"]["code"] == "INVALID_INVENTORY_CHECKED_BY"
    # A valid checker still succeeds and is stored without surrounding spaces.
    response = submit_check(client, fridge, labels=["A1"], by="  night-a ")
    assert response.status_code == 201, response.text
    assert response.json()["checked_by"] == "night-a"
    checks, _items = all_check_rows()
    assert checks == 1


def test_sequence_migration_backfills_per_location_gapless_and_downgrades(
    tmp_path,
    monkeypatch,
):
    from alembic import command
    from sqlalchemy import create_engine
    from sqlalchemy import text as sql_text

    from app.config import get_settings

    from .test_temperature_observations import _alembic_config

    db_path = tmp_path / "migration-inventory-sequence.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    get_settings.cache_clear()
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "20260910_0005")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")

    with engine.begin() as conn:
        conn.execute(
            sql_text(
                "INSERT INTO locations (id, code, name, is_cold_storage) "
                "VALUES ('loc-1', 'FRZ1', '冷冻柜1', 1), ('loc-2', 'FRZ2', '冷冻柜2', 1)"
            )
        )
        stamp = "2026-09-10T10:00:00+00:00"

        def insert_check(check_id: str, location_id: str) -> None:
            conn.execute(
                sql_text(
                    "INSERT INTO location_inventory_checks (id, location_id, checked_by, "
                    "created_at, matched_count, missing_count, misplaced_count, "
                    "unknown_count, scanned_count) VALUES (:id, :loc, 'night-a', :at, "
                    "0, 0, 0, 0, 0)"
                ),
                {"id": check_id, "loc": location_id, "at": stamp},
            )

        # Two checks share one created_at at loc-1; loc-2 has an independent run.
        insert_check("check-a", "loc-1")
        insert_check("check-b", "loc-1")
        insert_check("check-c", "loc-2")

    command.upgrade(cfg, "20260910_0006")
    with engine.begin() as conn:
        rows = conn.execute(
            sql_text(
                "SELECT id, sequence_number FROM location_inventory_checks "
                "ORDER BY location_id, sequence_number"
            )
        ).all()
        assert [tuple(row) for row in rows] == [
            ("check-a", 1),
            ("check-b", 2),
            ("check-c", 1),
        ]
        columns = {
            row[1]
            for row in conn.execute(sql_text("PRAGMA table_info(location_inventory_checks)"))
        }
        assert "sequence_number" in columns

    command.downgrade(cfg, "20260910_0005")
    with engine.begin() as conn:
        columns = {
            row[1]
            for row in conn.execute(sql_text("PRAGMA table_info(location_inventory_checks)"))
        }
        assert "sequence_number" not in columns
    get_settings.cache_clear()
