from datetime import UTC, datetime, timedelta

import pytest

from app.main import clock
from app.temperature import classify_temperature, parse_temperature_zone

from .test_transitions import start_handoff


def observe(
    client,
    container_id,
    *,
    temperature=5.0,
    by="night-a",
    at=None,
    note=None,
):
    if at is None:
        at = datetime.now(UTC) - timedelta(milliseconds=100)
    body = {
        "temperature_c": temperature,
        "measured_by": by,
        "observed_at": at.isoformat(),
        "note": note,
    }
    return client.post(
        f"/api/containers/{container_id}/temperature-observations", json=body
    )


def observations(detail):
    return detail["temperature_observations"]


def temperature_events(detail):
    return [e for e in detail["timeline"] if e["event_type"] == "temperature_recorded"]


def test_batch_create_accepts_numeric_celsius_bounds_and_legacy_zone_text(client):
    response = client.post(
        "/api/batches",
        json={
            "accession_number": "B-NUMERIC",
            "temp_min_c": 2,
            "temp_max_c": 8,
            "max_out_minutes": 30,
            "created_by": "alice",
            "containers": [{"label": "TUBE-A", "initial_location_code": "FRIDGE"}],
        },
    )
    assert response.status_code == 201, response.text
    batch = response.json()
    assert batch["temp_min_c"] == 2.0
    assert batch["temp_max_c"] == 8.0
    assert batch["temperature_zone"] == "2–8°C"

    legacy = client.post(
        "/api/batches",
        json={
            "accession_number": "B-LEGACY",
            "temperature_zone": "2–8°C",
            "max_out_minutes": 30,
            "created_by": "alice",
            "containers": [{"label": "TUBE-B", "initial_location_code": "FRIDGE"}],
        },
    )
    assert legacy.status_code == 201, legacy.text
    assert legacy.json()["temp_min_c"] == 2.0
    assert legacy.json()["temp_max_c"] == 8.0


def test_unparseable_zone_fails_validation(client):
    response = client.post(
        "/api/batches",
        json={
            "accession_number": "B-BAD",
            "temperature_zone": "ambient",
            "max_out_minutes": 30,
            "created_by": "alice",
            "containers": [{"label": "TUBE-A", "initial_location_code": "FRIDGE"}],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("reading", [2.0, 8.0, 5.0])
def test_boundary_values_and_midrange_are_normal(
    client, batch_factory, mutable_clock, monkeypatch, reading
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    response = observe(
        client,
        batch["containers"][0]["id"],
        temperature=reading,
        at=mutable_clock.value,
    )
    assert response.status_code == 201, response.text
    detail = response.json()
    assert observations(detail)[0]["verdict"] == "normal"
    assert detail["disposition"] == "active"
    assert temperature_events(detail)[0]["details"]["verdict"] == "normal"


@pytest.mark.parametrize("reading", [1.9, 8.1, -4.0, 25.0])
def test_out_of_range_marks_review_but_keeps_position_and_timing(
    client, batch_factory, mutable_clock, monkeypatch, reading
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory(location="FRIDGE")
    container_id = batch["containers"][0]["id"]
    measured_at = mutable_clock.value

    response = observe(client, container_id, temperature=reading, at=measured_at)
    assert response.status_code == 201, response.text
    detail = response.json()

    record = observations(detail)[0]
    assert record["verdict"] == "out_of_range"
    assert record["temperature_c"] == reading
    assert record["measured_by"] == "night-a"
    assert datetime.fromisoformat(record["observed_at"]) == measured_at
    assert detail["disposition"] == "review"

    container = next(c for c in detail["containers"] if c["id"] == container_id)
    # No side effects: position, exposure timer and handoffs are untouched.
    assert container["current_location"]["code"] == "FRIDGE"
    assert container["out_since"] is None
    assert container["accumulated_out_seconds"] == 0
    assert container["total_out_seconds"] == 0

    events = temperature_events(detail)
    assert len(events) == 1
    event = events[0]
    assert event["container_id"] == container_id
    assert event["actor"] == "night-a"
    assert event["details"]["temperature_c"] == reading


def test_out_of_range_write_is_atomic_when_the_timeline_event_fails(
    client, batch_factory, mutable_clock, monkeypatch
):
    # If the single timeline event cannot be written, the observation and the
    # review disposition must roll back together — never a partial result.
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    container_id = batch["containers"][0]["id"]

    from app import service

    def exploding_event(*_args, **_kwargs):
        raise RuntimeError("timeline store unavailable")

    monkeypatch.setattr(service, "_event", exploding_event)

    with pytest.raises(RuntimeError):
        observe(
            client,
            container_id,
            temperature=30.0,
            at=mutable_clock.value,
        )

    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert detail["disposition"] == "active"
    assert observations(detail) == []
    assert temperature_events(detail) == []


def test_sealed_container_is_rejected_with_the_replacement_error(client, batch_factory):
    batch = batch_factory()
    old_id = batch["containers"][0]["id"]
    replaced = client.post(
        f"/api/containers/{old_id}/replace",
        json={"new_label": "TUBE-B", "actor": "carol", "reason": "壳裂"},
    )
    assert replaced.status_code == 200

    response = observe(client, old_id, temperature=5.0)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONTAINER_ALREADY_REPLACED"

    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert observations(detail) == []
    assert temperature_events(detail) == []


def test_future_measurement_time_is_rejected(client, batch_factory, mutable_clock, monkeypatch):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    future = mutable_clock.value + timedelta(minutes=1)

    response = observe(client, batch["containers"][0]["id"], temperature=5.0, at=future)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_OBSERVED_AT"


def test_measurement_before_batch_creation_is_rejected(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    created = datetime.fromisoformat(batch["created_at"])
    before = created - timedelta(seconds=1)

    response = observe(client, batch["containers"][0]["id"], temperature=5.0, at=before)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_OBSERVED_AT"


def test_rejected_write_leaves_no_partial_results(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    container_id = batch["containers"][0]["id"]

    future = mutable_clock.value + timedelta(days=1)
    blocked = observe(client, container_id, temperature=5.0, at=future)
    assert blocked.status_code == 422

    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert observations(detail) == []
    assert temperature_events(detail) == []
    assert detail["disposition"] == "active"


def test_observations_are_returned_newest_first_and_events_stay_unique(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    container_id = batch["containers"][0]["id"]

    first_at = mutable_clock.value + timedelta(minutes=10)
    second_at = mutable_clock.value + timedelta(minutes=30)
    mutable_clock.value = second_at + timedelta(minutes=5)
    assert observe(client, container_id, temperature=5.0, by="a", at=first_at).status_code == 201
    assert observe(client, container_id, temperature=9.5, by="b", at=second_at).status_code == 201

    detail = client.get(f"/api/batches/{batch['id']}").json()
    records = observations(detail)
    assert [r["observed_at"] for r in records] == sorted(
        (r["observed_at"] for r in records), reverse=True
    )
    assert records[0]["temperature_c"] == 9.5
    assert records[0]["verdict"] == "out_of_range"
    assert records[1]["verdict"] == "normal"
    assert len(temperature_events(detail)) == 2
    # The out-of-range verdict moved the batch to review.
    assert detail["disposition"] == "review"


def test_temperature_recording_does_not_interfere_with_pending_handoff(
    client, batch_factory, mutable_clock, monkeypatch
):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    handoff = start_handoff(client, batch)
    response = observe(
        client, batch["containers"][0]["id"], temperature=5.0, at=mutable_clock.value
    )
    assert response.status_code == 201, response.text
    readback = client.get(f"/api/handoffs/{handoff['id']}").json()
    assert readback["status"] == "pending"


def test_blank_note_is_normalised_to_none(client, batch_factory, mutable_clock, monkeypatch):
    monkeypatch.setattr(clock, "now", mutable_clock.now)
    batch = batch_factory()
    response = observe(
        client,
        batch["containers"][0]["id"],
        temperature=5.0,
        note="   ",
        at=mutable_clock.value,
    )
    assert response.status_code == 201
    assert temperature_events(response.json())[0]["note"] is None
    assert observations(response.json())[0]["note"] is None


def test_zone_parser_supports_legacy_notations():
    assert parse_temperature_zone("2–8°C") == (2.0, 8.0)
    assert parse_temperature_zone("2-8C") == (2.0, 8.0)
    assert parse_temperature_zone(" 2 - 8 ") == (2.0, 8.0)
    assert parse_temperature_zone("-25～-15℃") == (-25.0, -15.0)
    assert parse_temperature_zone("2,5–7,5度") == (2.5, 7.5)
    with pytest.raises(ValueError):
        parse_temperature_zone("room temperature")
    with pytest.raises(ValueError):
        parse_temperature_zone("9-2°C")
    assert classify_temperature(2.0, 2.0, 8.0) == "normal"
    assert classify_temperature(8.0, 2.0, 8.0) == "normal"
    assert classify_temperature(8.01, 2.0, 8.0) == "out_of_range"


def _alembic_config(db_path):
    from pathlib import Path

    from alembic.config import Config

    backend_root = Path(__file__).parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return cfg


def test_migration_backfills_legacy_zones(tmp_path, monkeypatch):
    from alembic import command
    from sqlalchemy import create_engine
    from sqlalchemy import text as sql_text

    from app.config import get_settings

    db_path = tmp_path / "migration-ledger.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    get_settings.cache_clear()
    cfg = _alembic_config(db_path)
    # Build the genuine pre-feature schema, including a legacy free-text batch.
    command.upgrade(cfg, "20260910_0003")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            sql_text(
                "INSERT INTO batches (id, accession_number, temperature_zone, max_out_minutes, "
                "disposition, created_at) VALUES (:id, :acc, :zone, 30, 'ACTIVE', :created)"
            ),
            {
                "id": "b1",
                "acc": "LEGACY-1",
                "zone": "2–8°C",
                "created": "2026-09-01 00:00:00.000000",
            },
        )

    command.upgrade(cfg, "20260910_0004")
    with engine.begin() as conn:
        row = conn.execute(
            sql_text("SELECT temp_min_c, temp_max_c FROM batches WHERE id = 'b1'")
        ).one()
        assert tuple(row) == (2.0, 8.0)
        tables = {
            row[0]
            for row in conn.execute(
                sql_text("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        }
        assert "temperature_observations" in tables
    get_settings.cache_clear()


def test_migration_fails_explicitly_on_unparseable_legacy_zone(tmp_path, monkeypatch):
    from alembic import command
    from sqlalchemy import create_engine
    from sqlalchemy import text as sql_text

    from app.config import get_settings

    db_path = tmp_path / "migration-bad.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    get_settings.cache_clear()
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "20260910_0003")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            sql_text(
                "INSERT INTO batches (id, accession_number, temperature_zone, max_out_minutes, "
                "disposition, created_at) VALUES (:id, :acc, :zone, 30, 'ACTIVE', :created)"
            ),
            {
                "id": "b1",
                "acc": "BAD-ZONE-1",
                "zone": "ambient",
                "created": "2026-09-01 00:00:00.000000",
            },
        )

    with pytest.raises(RuntimeError, match="Cannot migrate temperature zones"):
        command.upgrade(cfg, "20260910_0004")
    get_settings.cache_clear()
