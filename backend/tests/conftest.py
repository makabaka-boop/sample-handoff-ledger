import os
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault(
    "DATABASE_URL", f"sqlite+pysqlite:///{Path(__file__).parent / 'test-ledger.db'}"
)
os.environ.setdefault(
    "HANDOFF_SIGNING_KEY", "test-signing-key-that-is-longer-than-thirty-two-bytes"
)
os.environ.setdefault("CORS_ORIGINS", "http://testserver")

import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Location


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 9, 20, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal.begin() as db:
        db.add_all(
            [
                Location(code="FRIDGE", name="冷藏冰箱", is_cold_storage=True),
                Location(code="BENCH", name="处理台", is_cold_storage=False),
                Location(code="WINDOW", name="交接窗", is_cold_storage=False),
            ]
        )
    yield


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def mutable_clock() -> MutableClock:
    return MutableClock()


def create_batch(client: TestClient, *, location: str = "FRIDGE", max_out: int = 30):
    response = client.post(
        "/api/batches",
        json={
            "accession_number": "BATCH-001",
            "temperature_zone": "2-8C",
            "max_out_minutes": max_out,
            "created_by": "alice",
            "containers": [{"label": "TUBE-A", "initial_location_code": location}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def batch_factory(client):
    return lambda **kwargs: create_batch(client, **kwargs)
