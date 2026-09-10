import uuid

from fastapi import Depends, FastAPI, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from .clock import clock
from .config import get_settings
from .database import get_db
from .errors import install_error_handlers
from .schemas import (
    BatchCreate,
    BatchDetail,
    BatchSummary,
    CancelRequest,
    ConfirmRequest,
    ContainerReplaceRequest,
    HandoffCreate,
    HandoffRead,
    HealthRead,
    InventoryCheckRead,
    InventoryCheckRequest,
    InventoryCheckSummary,
    LocationCreate,
    LocationRead,
    MarkAnomalyRequest,
    RejectRequest,
    ReopenRequest,
    ResolveAnomalyRequest,
    TemperatureObservationRequest,
)
from .service import (
    cancel_handoff,
    confirm_handoff,
    create_batch,
    create_handoff,
    create_inventory_check,
    create_location,
    get_batch,
    get_handoff,
    get_inventory_check,
    list_batches,
    list_handoffs,
    list_location_inventory_checks,
    list_locations,
    mark_anomaly,
    record_temperature_observation,
    reject_handoff,
    reopen_handoff,
    replace_container,
    resolve_anomaly,
)

settings = get_settings()
app = FastAPI(title="Sample Handoff Ledger", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)
install_error_handlers(app)


@app.middleware("http")
async def tracing(request: Request, call_next):
    request.state.trace_id = request.headers.get("X-Trace-ID", str(uuid.uuid4()))
    response: Response = await call_next(request)
    response.headers["X-Trace-ID"] = request.state.trace_id
    return response


@app.get("/health", response_model=HealthRead)
def health(db: Session = Depends(get_db)) -> HealthRead:
    db.execute(text("SELECT 1"))
    return HealthRead(status="ok", database="ok")


@app.get("/api/locations", response_model=list[LocationRead])
def locations(db: Session = Depends(get_db)):
    return list_locations(db)


@app.post("/api/locations", response_model=LocationRead, status_code=status.HTTP_201_CREATED)
def add_location(payload: LocationCreate, db: Session = Depends(get_db)):
    return create_location(db, payload.code, payload.name, payload.is_cold_storage)


@app.get(
    "/api/locations/{location_id}/inventory-checks",
    response_model=list[InventoryCheckSummary],
)
def location_inventory_checks(location_id: str, db: Session = Depends(get_db)):
    return list_location_inventory_checks(db, location_id)


@app.post(
    "/api/locations/{location_id}/inventory-checks",
    response_model=InventoryCheckRead,
    status_code=status.HTTP_201_CREATED,
)
def add_inventory_check(
    location_id: str, payload: InventoryCheckRequest, db: Session = Depends(get_db)
):
    return create_inventory_check(db, location_id, payload, clock)


@app.get(
    "/api/inventory-checks/{check_id}",
    response_model=InventoryCheckRead,
)
def inventory_check_detail(check_id: str, db: Session = Depends(get_db)):
    return get_inventory_check(db, check_id)


@app.post("/api/batches", response_model=BatchSummary, status_code=status.HTTP_201_CREATED)
def add_batch(payload: BatchCreate, db: Session = Depends(get_db)):
    return create_batch(db, payload, clock)


@app.get("/api/batches", response_model=list[BatchSummary])
def batches(db: Session = Depends(get_db)):
    return list_batches(db, clock)


@app.get("/api/batches/{batch_id}", response_model=BatchDetail)
def batch_detail(batch_id: str, db: Session = Depends(get_db)):
    return get_batch(db, batch_id, clock)


@app.post("/api/containers/{container_id}/replace", response_model=BatchDetail)
def replace(container_id: str, payload: ContainerReplaceRequest, db: Session = Depends(get_db)):
    return replace_container(db, container_id, payload, clock)


@app.post(
    "/api/containers/{container_id}/temperature-observations",
    response_model=BatchDetail,
    status_code=status.HTTP_201_CREATED,
)
def add_temperature_observation(
    container_id: str, payload: TemperatureObservationRequest, db: Session = Depends(get_db)
):
    return record_temperature_observation(db, container_id, payload, clock)


@app.post("/api/handoffs", response_model=HandoffRead, status_code=status.HTTP_201_CREATED)
def add_handoff(payload: HandoffCreate, db: Session = Depends(get_db)):
    return create_handoff(db, payload, clock, settings)


@app.get("/api/handoffs", response_model=list[HandoffRead])
def handoffs(
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
):
    return list_handoffs(db, clock, status_filter)


@app.get("/api/handoffs/{handoff_id}", response_model=HandoffRead)
def handoff_detail(handoff_id: str, db: Session = Depends(get_db)):
    return get_handoff(db, handoff_id, clock)


@app.post("/api/handoffs/confirm", response_model=HandoffRead)
def confirm(payload: ConfirmRequest, db: Session = Depends(get_db)):
    return confirm_handoff(db, payload, clock, settings)


@app.post("/api/handoffs/reject", response_model=HandoffRead)
def reject(payload: RejectRequest, db: Session = Depends(get_db)):
    return reject_handoff(db, payload, clock, settings)


@app.post("/api/handoffs/{handoff_id}/cancel", response_model=HandoffRead)
def cancel(handoff_id: str, payload: CancelRequest, db: Session = Depends(get_db)):
    return cancel_handoff(db, handoff_id, payload, clock)


@app.post("/api/handoffs/{handoff_id}/anomaly", response_model=HandoffRead)
def anomaly(handoff_id: str, payload: MarkAnomalyRequest, db: Session = Depends(get_db)):
    return mark_anomaly(db, handoff_id, payload, clock)


@app.post("/api/handoffs/{handoff_id}/resolve", response_model=HandoffRead)
def resolve(handoff_id: str, payload: ResolveAnomalyRequest, db: Session = Depends(get_db)):
    return resolve_anomaly(db, handoff_id, payload, clock)


@app.post(
    "/api/handoffs/{handoff_id}/reopen",
    response_model=HandoffRead,
    status_code=status.HTTP_201_CREATED,
)
def reopen(handoff_id: str, payload: ReopenRequest, db: Session = Depends(get_db)):
    return reopen_handoff(db, handoff_id, payload, clock, settings)
