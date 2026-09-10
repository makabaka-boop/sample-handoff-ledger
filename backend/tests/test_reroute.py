from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from app.main import clock

from .test_rejections import reject
from .test_transitions import start_handoff


def make_batch(client, accession):
    response = client.post(
        "/api/batches",
        json={
            "accession_number": accession,
            "temperature_zone": "2-8C",
            "max_out_minutes": 30,
            "created_by": "alice",
            "containers": [{"label": "TUBE-A", "initial_location_code": "FRIDGE"}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def reroute(client, handoff_id, *, actor="alice", target="WINDOW", reason="处理台临时停用"):
    return client.post(
        f"/api/handoffs/{handoff_id}/reroute",
        json={"actor": actor, "to_location_code": target, "reason": reason},
    )


def confirm(client, code, *, by="bob"):
    return client.post("/api/handoffs/confirm", json={"code": code, "received_by": by})


def test_reroute_retargets_pending_handoff_and_original_code_still_receives(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    handoff = start_handoff(client, batch)
    assert handoff["original_to_location"] is None
    assert handoff["rerouted_by"] is None
    assert handoff["rerouted_at"] is None
    assert handoff["reroute_reason"] is None

    response = reroute(client, handoff["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["to_location"]["code"] == "WINDOW"
    assert body["original_to_location"]["code"] == "BENCH"
    assert body["rerouted_by"] == "alice"
    assert body["rerouted_at"] is not None
    assert body["reroute_reason"] == "处理台临时停用"
    # The code and the deadline are not rebuilt by a reroute. (SQLite drops the
    # tzinfo on round-trip; both responses denote the same instant.)
    assert datetime.fromisoformat(body["expires_at"]).replace(tzinfo=UTC) == datetime.fromisoformat(
        handoff["expires_at"]
    )
    assert body["receipt_code"] is None

    received = confirm(client, handoff["receipt_code"])
    assert received.status_code == 200, received.text
    assert received.json()["status"] == "received"
    assert received.json()["to_location"]["code"] == "WINDOW"

    detail = client.get(f"/api/batches/{batch['id']}").json()
    # The container only ever moved to the rerouted target.
    assert detail["containers"][0]["current_location"]["code"] == "WINDOW"
    rerouted = [e for e in detail["timeline"] if e["event_type"] == "handoff_rerouted"]
    received_events = [e for e in detail["timeline"] if e["event_type"] == "handoff_received"]
    assert len(rerouted) == 1
    assert len(received_events) == 1
    assert rerouted[0]["actor"] == "alice"
    assert rerouted[0]["details"] == {
        "from": "FRIDGE",
        "previous_to": "BENCH",
        "to": "WINDOW",
        "reason": "处理台临时停用",
    }
    assert received_events[0]["details"]["to"] == "WINDOW"


def test_second_reroute_keeps_the_creation_time_target_as_original(client, batch_factory):
    handoff = start_handoff(client, batch_factory())
    first = reroute(client, handoff["id"], target="WINDOW")
    assert first.status_code == 200
    second = reroute(client, handoff["id"], target="BENCH", reason="处理台恢复")
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["to_location"]["code"] == "BENCH"
    assert body["original_to_location"]["code"] == "BENCH"
    assert body["reroute_reason"] == "处理台恢复"
    detail = client.get(f"/api/batches/{handoff['batch_id']}").json()
    assert len([e for e in detail["timeline"] if e["event_type"] == "handoff_rerouted"]) == 2


def test_only_initiator_can_reroute(client, batch_factory):
    handoff = start_handoff(client, batch_factory())
    denied = reroute(client, handoff["id"], actor="bob")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "NOT_HANDOFF_CREATOR"
    unchanged = client.get(f"/api/handoffs/{handoff['id']}").json()
    assert unchanged["to_location"]["code"] == "BENCH"
    assert unchanged["rerouted_by"] is None


def test_reroute_rejects_unknown_equal_or_mismatched_targets_without_side_effects(
    client, batch_factory
):
    client.post(
        "/api/locations",
        json={"code": "COLDROOM", "name": "备用冷藏间", "is_cold_storage": True},
    )
    handoff = start_handoff(client, batch_factory())

    missing = reroute(client, handoff["id"], target="NOPE")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "LOCATION_NOT_FOUND"

    source = reroute(client, handoff["id"], target="FRIDGE")
    assert source.status_code == 422
    assert source.json()["error"]["code"] == "SAME_LOCATION"

    unchanged = reroute(client, handoff["id"], target="BENCH")
    assert unchanged.status_code == 422
    assert unchanged.json()["error"]["code"] == "REROUTE_TARGET_UNCHANGED"

    mismatched = reroute(client, handoff["id"], target="COLDROOM")
    assert mismatched.status_code == 422
    assert mismatched.json()["error"]["code"] == "REROUTE_ENVIRONMENT_MISMATCH"

    # None of the rejected attempts left provenance fields or timeline events.
    handoff_after = client.get(f"/api/handoffs/{handoff['id']}").json()
    assert handoff_after["to_location"]["code"] == "BENCH"
    assert handoff_after["original_to_location"] is None
    assert handoff_after["rerouted_by"] is None
    assert handoff_after["rerouted_at"] is None
    assert handoff_after["reroute_reason"] is None
    detail = client.get(f"/api/batches/{handoff['batch_id']}").json()
    assert [e for e in detail["timeline"] if e["event_type"] == "handoff_rerouted"] == []


def test_cold_target_cannot_reroute_to_warm_location(client, batch_factory):
    client.post(
        "/api/locations",
        json={"code": "COLDROOM", "name": "备用冷藏间", "is_cold_storage": True},
    )
    handoff = start_handoff(client, batch_factory(), destination="COLDROOM")
    mismatched = reroute(client, handoff["id"], target="WINDOW")
    assert mismatched.status_code == 422
    assert mismatched.json()["error"]["code"] == "REROUTE_ENVIRONMENT_MISMATCH"
    ok = reroute(client, handoff["id"], target="FRIDGE")
    # FRIDGE is the source here only if the container started elsewhere; it did not.
    assert ok.status_code == 422
    assert ok.json()["error"]["code"] == "SAME_LOCATION"


def test_reroute_after_confirm_cancel_or_reject_returns_the_business_conflict(client):
    confirmed = start_handoff(client, make_batch(client, "REROUTE-CONFIRMED"))
    confirm(client, confirmed["receipt_code"])
    response = reroute(client, confirmed["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "HANDOFF_ALREADY_RECEIVED"

    cancelled = start_handoff(client, make_batch(client, "REROUTE-CANCELLED"))
    client.post(f"/api/handoffs/{cancelled['id']}/cancel", json={"actor": "alice"})
    response = reroute(client, cancelled["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "HANDOFF_CANCELLED"

    rejected = start_handoff(client, make_batch(client, "REROUTE-REJECTED"))
    reject(client, rejected["receipt_code"])
    response = reroute(client, rejected["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "HANDOFF_REJECTED"


def test_expired_handoff_cannot_reroute_and_becomes_anomaly(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    handoff = start_handoff(client, batch_factory(), ttl=1)
    mutable_clock.value += timedelta(seconds=60)

    response = reroute(client, handoff["id"])
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "HANDOFF_EXPIRED"
    after = client.get(f"/api/handoffs/{handoff['id']}").json()
    assert after["persisted_status"] == "anomaly"
    assert after["to_location"]["code"] == "BENCH"
    assert after["rerouted_by"] is None
    detail = client.get(f"/api/batches/{handoff['batch_id']}").json()
    assert [e for e in detail["timeline"] if e["event_type"] == "handoff_rerouted"] == []


def test_reroute_requires_container_still_at_source(client, batch_factory):
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Container, Location

    handoff = start_handoff(client, batch_factory())
    with SessionLocal.begin() as db:
        container = db.get(Container, handoff["container_id"])
        window = db.scalar(select(Location).where(Location.code == "WINDOW"))
        container.current_location_id = window.id

    response = reroute(client, handoff["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LOCATION_MISMATCH"
    after = client.get(f"/api/handoffs/{handoff['id']}").json()
    assert after["rerouted_by"] is None


def test_reroute_and_confirm_compete_with_one_adjudicated_outcome(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    handoff = start_handoff(client, batch)

    def worker(kind):
        if kind == "reroute":
            return kind, reroute(client, handoff["id"])
        return kind, confirm(client, handoff["receipt_code"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = dict(executor.map(worker, ["reroute", "confirm"]))

    reroute_response = outcomes["reroute"]
    confirm_response = outcomes["confirm"]
    detail = client.get(f"/api/batches/{batch['id']}").json()
    rerouted = [e for e in detail["timeline"] if e["event_type"] == "handoff_rerouted"]
    received = [e for e in detail["timeline"] if e["event_type"] == "handoff_received"]
    assert len(received) == 1
    if confirm_response.status_code == 200 and reroute_response.status_code == 409:
        # Confirmation won the row lock first: the reroute sees the received fact.
        assert reroute_response.json()["error"]["code"] == "HANDOFF_ALREADY_RECEIVED"
        assert detail["containers"][0]["current_location"]["code"] == "BENCH"
        assert rerouted == []
        assert received[0]["details"]["to"] == "BENCH"
    else:
        # Reroute won first: the original code still receives, at the new target.
        assert reroute_response.status_code == 200
        assert confirm_response.status_code == 200
        assert confirm_response.json()["to_location"]["code"] == "WINDOW"
        assert detail["containers"][0]["current_location"]["code"] == "WINDOW"
        assert len(rerouted) == 1
        assert received[0]["details"]["to"] == "WINDOW"


def test_reroute_and_cancel_compete_with_one_adjudicated_outcome(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    handoff = start_handoff(client, batch_factory())

    def worker(kind):
        if kind == "reroute":
            return kind, reroute(client, handoff["id"])
        return kind, client.post(
            f"/api/handoffs/{handoff['id']}/cancel", json={"actor": "alice"}
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = dict(executor.map(worker, ["reroute", "cancel"]))

    reroute_response = outcomes["reroute"]
    cancel_response = outcomes["cancel"]
    detail = client.get(f"/api/batches/{handoff['batch_id']}").json()
    rerouted = [e for e in detail["timeline"] if e["event_type"] == "handoff_rerouted"]
    if cancel_response.status_code == 200 and reroute_response.status_code == 409:
        assert reroute_response.json()["error"]["code"] == "HANDOFF_CANCELLED"
        assert rerouted == []
    else:
        # Reroute committed first; the cancel then applies to the retargeted
        # handoff, which a fresh read shows as cancelled towards WINDOW.
        assert reroute_response.status_code == 200
        assert cancel_response.status_code == 200
        after = client.get(f"/api/handoffs/{handoff['id']}").json()
        assert after["status"] == "cancelled"
        assert after["to_location"]["code"] == "WINDOW"
        assert len(rerouted) == 1


def test_reroute_and_reject_compete_with_one_adjudicated_outcome(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    handoff = start_handoff(client, batch_factory())

    def worker(kind):
        if kind == "reroute":
            return kind, reroute(client, handoff["id"])
        return kind, reject(client, handoff["receipt_code"], by="night-a")

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = dict(executor.map(worker, ["reroute", "reject"]))

    reroute_response = outcomes["reroute"]
    reject_response = outcomes["reject"]
    detail = client.get(f"/api/batches/{handoff['batch_id']}").json()
    rerouted = [e for e in detail["timeline"] if e["event_type"] == "handoff_rerouted"]
    rejected = [e for e in detail["timeline"] if e["event_type"] == "handoff_rejected"]
    if reject_response.status_code == 200 and reroute_response.status_code == 409:
        assert reroute_response.json()["error"]["code"] == "HANDOFF_REJECTED"
        assert rerouted == []
        assert len(rejected) == 1
    else:
        assert reroute_response.status_code == 200
        assert reject_response.status_code == 200
        assert len(rerouted) == 1
        assert len(rejected) == 1


def test_migration_keeps_legacy_handoffs_readable(tmp_path, monkeypatch):
    from alembic import command
    from sqlalchemy import create_engine
    from sqlalchemy import text as sql_text

    from app.config import get_settings

    from .test_temperature_observations import _alembic_config

    db_path = tmp_path / "migration-reroute.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    get_settings.cache_clear()
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "20260910_0006")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            sql_text(
                "INSERT INTO batches (id, accession_number, temperature_zone, temp_min_c, "
                "temp_max_c, max_out_minutes, disposition, created_at) VALUES "
                "('b1', 'LEGACY-1', '2-8C', 2.0, 8.0, 30, 'ACTIVE', '2026-09-01 00:00:00')"
            )
        )
        conn.execute(
            sql_text(
                "INSERT INTO containers (id, batch_id, label, current_location_id, "
                "accumulated_out_seconds, status, updated_at) VALUES "
                "('c1', 'b1', 'TUBE-A', '00000000-0000-0000-0000-000000000001', 0, "
                "'ACTIVE', '2026-09-01 00:00:00')"
            )
        )
        conn.execute(
            sql_text(
                "INSERT INTO handoffs (id, batch_id, container_id, from_location_id, "
                "to_location_id, code_digest, created_by, status, created_at, expires_at) "
                "VALUES ('h1', 'b1', 'c1', '00000000-0000-0000-0000-000000000001', "
                "'00000000-0000-0000-0000-000000000002', 'digest-1', 'alice', 'PENDING', "
                "'2026-09-01 00:00:00', '2026-09-01 00:10:00')"
            )
        )

    command.upgrade(cfg, "20260910_0007")
    with engine.begin() as conn:
        row = conn.execute(
            sql_text(
                "SELECT to_location_id, original_to_location_id, rerouted_by, rerouted_at, "
                "reroute_reason FROM handoffs WHERE id = 'h1'"
            )
        ).one()
        assert row == (
            "00000000-0000-0000-0000-000000000002",
            None,
            None,
            None,
            None,
        )
    command.downgrade(cfg, "20260910_0006")
    with engine.begin() as conn:
        columns = {
            row[1] for row in conn.execute(sql_text("PRAGMA table_info(handoffs)"))
        }
        assert "original_to_location_id" not in columns
        assert "rerouted_by" not in columns
    get_settings.cache_clear()
