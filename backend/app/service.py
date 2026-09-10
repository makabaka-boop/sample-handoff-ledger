from __future__ import annotations

import threading
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from .clock import Clock
from .config import Settings
from .errors import DomainError
from .models import (
    Batch,
    BatchDisposition,
    Container,
    ContainerStatus,
    Handoff,
    HandoffStatus,
    InventoryCheckCategory,
    Location,
    LocationInventoryCheck,
    LocationInventoryCheckItem,
    TemperatureObservation,
    TemperatureVerdict,
    TimelineEvent,
)
from .schemas import (
    BatchCreate,
    BatchDetail,
    BatchSummary,
    CancelRequest,
    ConfirmRequest,
    ContainerRead,
    ContainerReplaceRequest,
    HandoffCreate,
    HandoffRead,
    InventoryCheckRead,
    InventoryCheckRequest,
    InventoryCheckSummary,
    LocationRead,
    MarkAnomalyRequest,
    RejectRequest,
    ReopenRequest,
    ResolveAnomalyRequest,
    TemperatureObservationRead,
    TemperatureObservationRequest,
    TimelineRead,
)
from .security import code_digest, generate_code
from .temperature import classify_temperature

_sqlite_code_action_locks: dict[str, threading.Lock] = {}
_sqlite_locks_guard = threading.Lock()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _seconds(start: datetime, end: datetime) -> int:
    return max(0, int((_aware(end) - _aware(start)).total_seconds()))


def _event(
    db: Session,
    *,
    batch_id: str,
    event_type: str,
    actor: str,
    at: datetime,
    container_id: str | None = None,
    handoff_id: str | None = None,
    observation_id: str | None = None,
    details: dict | None = None,
    note: str | None = None,
) -> None:
    db.add(
        TimelineEvent(
            batch_id=batch_id,
            container_id=container_id,
            handoff_id=handoff_id,
            observation_id=observation_id,
            event_type=event_type,
            actor=actor,
            occurred_at=at,
            details=details or {},
            note=note,
        )
    )


def _location_by_code(db: Session, code: str) -> Location:
    location = db.scalar(select(Location).where(Location.code == code))
    if location is None:
        raise DomainError(404, "LOCATION_NOT_FOUND", f"Location '{code}' does not exist.")
    return location


def _exposure(container: Container, now: datetime) -> tuple[int, int]:
    current = _seconds(container.out_since, now) if container.out_since else 0
    return current, container.accumulated_out_seconds + current


def _container_read(container: Container, batch: Batch, now: datetime) -> ContainerRead:
    current, total = _exposure(container, now)
    limit = batch.max_out_minutes * 60
    return ContainerRead(
        id=container.id,
        label=container.label,
        current_location=LocationRead.model_validate(container.current_location),
        accumulated_out_seconds=container.accumulated_out_seconds,
        current_out_seconds=current,
        total_out_seconds=total,
        max_out_seconds=limit,
        exposure_exceeded=total >= limit,
        out_since=container.out_since,
        updated_at=container.updated_at,
        status=container.status,
        replacement_container_id=container.replacement_container_id,
        replaced_by=container.replaced_by,
        replaced_at=container.replaced_at,
        replacement_reason=container.replacement_reason,
    )


def _has_unresolved_anomaly(db: Session, batch_id: str, now: datetime) -> bool:
    return (
        db.scalar(
            select(func.count(Handoff.id)).where(
                Handoff.batch_id == batch_id,
                Handoff.resolved_at.is_(None),
                (
                    (Handoff.status == HandoffStatus.ANOMALY)
                    | and_(
                        Handoff.status == HandoffStatus.PENDING,
                        Handoff.expires_at <= now,
                    )
                ),
            )
        )
        or 0
    ) > 0


def _handoff_read(
    handoff: Handoff,
    batch: Batch,
    now: datetime,
    *,
    receipt_code: str | None = None,
    replayed: bool = False,
) -> HandoffRead:
    effective = handoff.status
    if effective == HandoffStatus.PENDING and _aware(handoff.expires_at) <= now:
        effective = HandoffStatus.ANOMALY
    _, exposure = _exposure(handoff.container, now)
    return HandoffRead(
        id=handoff.id,
        batch_id=batch.id,
        accession_number=batch.accession_number,
        container_id=handoff.container_id,
        container_label=handoff.container.label,
        from_location=LocationRead.model_validate(handoff.from_location),
        to_location=LocationRead.model_validate(handoff.to_location),
        created_by=handoff.created_by,
        received_by=handoff.received_by,
        cancelled_by=handoff.cancelled_by,
        rejected_by=handoff.rejected_by,
        rejected_at=handoff.rejected_at,
        reject_reason=handoff.reject_reason,
        reject_note=handoff.reject_note,
        status=effective,
        persisted_status=handoff.status,
        created_at=handoff.created_at,
        expires_at=handoff.expires_at,
        received_at=handoff.received_at,
        cancelled_at=handoff.cancelled_at,
        anomaly_at=handoff.anomaly_at,
        anomaly_reason=handoff.anomaly_reason
        or ("HANDOFF_EXPIRED" if effective == HandoffStatus.ANOMALY else None),
        resolved_at=handoff.resolved_at,
        resolution=handoff.resolution,
        successor_id=handoff.successor_id,
        remaining_seconds=max(0, _seconds(now, handoff.expires_at)),
        server_time=now,
        exposure_seconds=exposure,
        exposure_limit_seconds=batch.max_out_minutes * 60,
        exposure_exceeded=exposure >= batch.max_out_minutes * 60,
        receipt_code=receipt_code,
        replayed=replayed,
    )


def list_locations(db: Session) -> list[Location]:
    return list(db.scalars(select(Location).order_by(Location.code)))


def create_location(db: Session, code: str, name: str, is_cold_storage: bool) -> Location:
    normalized = code.upper()
    with db.begin():
        if db.scalar(select(Location).where(Location.code == normalized)):
            raise DomainError(409, "LOCATION_EXISTS", "That location code already exists.")
        location = Location(code=normalized, name=name, is_cold_storage=is_cold_storage)
        db.add(location)
        db.flush()
    return location


def create_batch(db: Session, payload: BatchCreate, clock: Clock) -> BatchSummary:
    now = clock.now()
    with db.begin():
        if db.scalar(select(Batch).where(Batch.accession_number == payload.accession_number)):
            raise DomainError(409, "BATCH_EXISTS", "That accession number already exists.")
        locations = {
            location.code: location
            for location in db.scalars(
                select(Location).where(
                    Location.code.in_(
                        [item.initial_location_code.upper() for item in payload.containers]
                    )
                )
            )
        }
        missing = sorted(
            {
                item.initial_location_code.upper()
                for item in payload.containers
                if item.initial_location_code.upper() not in locations
            }
        )
        if missing:
            raise DomainError(
                422,
                "UNKNOWN_INITIAL_LOCATION",
                "One or more initial locations do not exist.",
                details={"codes": missing},
            )
        batch = Batch(
            accession_number=payload.accession_number,
            temperature_zone=payload.temperature_zone,
            temp_min_c=payload.temp_min_c,
            temp_max_c=payload.temp_max_c,
            max_out_minutes=payload.max_out_minutes,
            created_at=now,
        )
        db.add(batch)
        db.flush()
        for item in payload.containers:
            location = locations[item.initial_location_code.upper()]
            container = Container(
                batch_id=batch.id,
                label=item.label,
                current_location_id=location.id,
                out_since=None if location.is_cold_storage else now,
                updated_at=now,
            )
            db.add(container)
            db.flush()
            _event(
                db,
                batch_id=batch.id,
                container_id=container.id,
                event_type="container_registered",
                actor=payload.created_by,
                at=now,
                details={"label": item.label, "location": location.code},
            )
        _event(
            db,
            batch_id=batch.id,
            event_type="batch_created",
            actor=payload.created_by,
            at=now,
            details={
                "accession_number": batch.accession_number,
                "temperature_zone": batch.temperature_zone,
                "max_out_minutes": batch.max_out_minutes,
            },
        )
    return get_batch(db, batch.id, clock, detail=False)


def list_batches(db: Session, clock: Clock) -> list[BatchSummary]:
    now = clock.now()
    batches = list(
        db.scalars(
            select(Batch)
            .options(selectinload(Batch.containers).selectinload(Container.current_location))
            .order_by(Batch.created_at.desc())
        )
    )
    return [
        BatchSummary(
            id=batch.id,
            accession_number=batch.accession_number,
            temperature_zone=batch.temperature_zone,
            temp_min_c=batch.temp_min_c,
            temp_max_c=batch.temp_max_c,
            max_out_minutes=batch.max_out_minutes,
            disposition=batch.disposition,
            created_at=batch.created_at,
            has_unresolved_anomaly=_has_unresolved_anomaly(db, batch.id, now),
            containers=[_container_read(container, batch, now) for container in batch.containers],
        )
        for batch in batches
    ]


def get_batch(
    db: Session, batch_id: str, clock: Clock, *, detail: bool = True
) -> BatchSummary | BatchDetail:
    now = clock.now()
    batch = db.scalar(
        select(Batch)
        .where(Batch.id == batch_id)
        .options(selectinload(Batch.containers).selectinload(Container.current_location))
    )
    if batch is None:
        raise DomainError(404, "BATCH_NOT_FOUND", "Batch does not exist.")
    common = dict(
        id=batch.id,
        accession_number=batch.accession_number,
        temperature_zone=batch.temperature_zone,
        temp_min_c=batch.temp_min_c,
        temp_max_c=batch.temp_max_c,
        max_out_minutes=batch.max_out_minutes,
        disposition=batch.disposition,
        created_at=batch.created_at,
        has_unresolved_anomaly=_has_unresolved_anomaly(db, batch.id, now),
        containers=[_container_read(container, batch, now) for container in batch.containers],
    )
    if not detail:
        return BatchSummary(**common)
    events = list(
        db.scalars(
            select(TimelineEvent)
            .where(TimelineEvent.batch_id == batch_id)
            .order_by(TimelineEvent.occurred_at.desc(), TimelineEvent.id.desc())
        )
    )
    observations = list(
        db.scalars(
            select(TemperatureObservation)
            .where(TemperatureObservation.batch_id == batch_id)
            .order_by(
                TemperatureObservation.observed_at.desc(),
                TemperatureObservation.id.desc(),
            )
        )
    )
    return BatchDetail(
        **common,
        timeline=[
            TimelineRead(
                id=event.id,
                container_id=event.container_id,
                handoff_id=event.handoff_id,
                event_type=event.event_type,
                actor=event.actor,
                occurred_at=event.occurred_at,
                details=event.details,
                note=event.note,
            )
            for event in events
        ],
        temperature_observations=[
            TemperatureObservationRead(
                id=observation.id,
                batch_id=observation.batch_id,
                container_id=observation.container_id,
                temperature_c=observation.temperature_c,
                verdict=observation.verdict.value,
                measured_by=observation.measured_by,
                observed_at=observation.observed_at,
                note=observation.note,
                created_at=observation.created_at,
                temp_min_c=batch.temp_min_c,
                temp_max_c=batch.temp_max_c,
            )
            for observation in observations
        ],
    )


def _container_action_lock(db: Session, key: str) -> threading.Lock | None:
    # SQLite serializes writers poorly under threads; mirror the receipt-code lock
    # so concurrent adjudications on one container remain deterministic in tests.
    if db.bind is None or db.bind.dialect.name != "sqlite":
        return None
    with _sqlite_locks_guard:
        return _sqlite_code_action_locks.setdefault(f"container:{key}", threading.Lock())


def replace_container(
    db: Session,
    container_id: str,
    payload: ContainerReplaceRequest,
    clock: Clock,
) -> BatchDetail:
    """Transload a damaged physical container: seal the old record, create an
    active successor that inherits position and exposure timing."""
    local_lock = _container_action_lock(db, container_id)
    if local_lock:
        local_lock.acquire()
    try:
        now = clock.now()
        with db.begin():
            # Same adjudication order as handoffs: container first, then batch.
            old = db.scalar(
                select(Container).where(Container.id == container_id).with_for_update()
            )
            if old is None:
                raise DomainError(404, "CONTAINER_NOT_FOUND", "Container does not exist.")
            if old.status == ContainerStatus.REPLACED:
                raise DomainError(
                    409,
                    "CONTAINER_ALREADY_REPLACED",
                    "This container was already sealed after transloading.",
                )
            batch = db.scalar(select(Batch).where(Batch.id == old.batch_id).with_for_update())
            assert batch is not None
            if batch.disposition != BatchDisposition.ACTIVE:
                raise DomainError(
                    409,
                    "BATCH_NOT_ACTIVE",
                    "The batch must be active before its containers can be transloaded.",
                    details={"disposition": batch.disposition.value},
                )
            pending = db.scalar(
                select(Handoff.id).where(
                    Handoff.container_id == old.id,
                    Handoff.status == HandoffStatus.PENDING,
                )
            )
            if pending:
                raise DomainError(
                    409,
                    "HANDOFF_ALREADY_PENDING",
                    "Receive or cancel the pending handoff before replacing the container.",
                )
            label_taken = db.scalar(
                select(Container.id).where(
                    Container.batch_id == batch.id,
                    func.lower(Container.label) == payload.new_label.casefold(),
                )
            )
            if label_taken:
                raise DomainError(
                    409,
                    "CONTAINER_LABEL_EXISTS",
                    "That label is already used by a container in this batch.",
                    details={"label": payload.new_label},
                )
            successor = Container(
                batch_id=batch.id,
                label=payload.new_label,
                current_location_id=old.current_location_id,
                # Timing continuity: the exposure clock never resets at transload.
                accumulated_out_seconds=old.accumulated_out_seconds,
                out_since=old.out_since,
                status=ContainerStatus.ACTIVE,
                updated_at=now,
            )
            db.add(successor)
            db.flush()
            old.status = ContainerStatus.REPLACED
            old.replacement_container_id = successor.id
            old.replaced_by = payload.actor
            old.replaced_at = now
            old.replacement_reason = payload.reason
            old.updated_at = now
            _event(
                db,
                batch_id=batch.id,
                container_id=old.id,
                event_type="container_replaced",
                actor=payload.actor,
                at=now,
                details={
                    "old_label": old.label,
                    "new_label": successor.label,
                    "new_container_id": successor.id,
                    "reason": payload.reason,
                    "accumulated_out_seconds": successor.accumulated_out_seconds,
                    "out_since": successor.out_since.isoformat()
                    if successor.out_since
                    else None,
                },
                note=payload.note,
            )
            batch_id = batch.id
    finally:
        if local_lock:
            local_lock.release()
    return get_batch(db, batch_id, clock)


def record_temperature_observation(
    db: Session,
    container_id: str,
    payload: TemperatureObservationRequest,
    clock: Clock,
) -> BatchDetail:
    """Adjudicate one manual celsius reading against the batch bounds.

    The observation and its single timeline event commit atomically. An out of
    range reading also sends the batch to review, but never touches the
    container's position, out-of-storage timer or any handoff.
    """
    local_lock = _container_action_lock(db, container_id)
    if local_lock:
        local_lock.acquire()
    batch_id = ""
    try:
        now = clock.now()
        with db.begin():
            # Same adjudication order as the other container actions.
            container = db.scalar(
                select(Container).where(Container.id == container_id).with_for_update()
            )
            if container is None:
                raise DomainError(404, "CONTAINER_NOT_FOUND", "Container does not exist.")
            if container.status == ContainerStatus.REPLACED:
                raise DomainError(
                    409,
                    "CONTAINER_ALREADY_REPLACED",
                    "This container was sealed after transloading; "
                    "measure the replacement container.",
                    details={"replacement_container_id": container.replacement_container_id},
                )
            batch = db.scalar(select(Batch).where(Batch.id == container.batch_id).with_for_update())
            assert batch is not None
            observed_at = _aware(payload.observed_at)
            if observed_at > now:
                raise DomainError(
                    422,
                    "INVALID_OBSERVED_AT",
                    "Measurement time cannot be later than the server's current time.",
                    details={
                        "observed_at": observed_at.isoformat(),
                        "server_time": now.isoformat(),
                    },
                )
            if observed_at < _aware(batch.created_at):
                raise DomainError(
                    422,
                    "INVALID_OBSERVED_AT",
                    "Measurement time cannot be earlier than the batch creation time.",
                    details={
                        "observed_at": observed_at.isoformat(),
                        "batch_created_at": _aware(batch.created_at).isoformat(),
                    },
                )
            verdict_value = classify_temperature(
                payload.temperature_c, batch.temp_min_c, batch.temp_max_c
            )
            verdict = TemperatureVerdict(verdict_value)
            observation = TemperatureObservation(
                batch_id=batch.id,
                container_id=container.id,
                temperature_c=payload.temperature_c,
                verdict=verdict,
                measured_by=payload.measured_by,
                observed_at=observed_at,
                note=payload.note,
                created_at=now,
            )
            db.add(observation)
            db.flush()
            if verdict == TemperatureVerdict.OUT_OF_RANGE:
                batch.disposition = BatchDisposition.REVIEW
            _event(
                db,
                batch_id=batch.id,
                container_id=container.id,
                observation_id=observation.id,
                event_type="temperature_recorded",
                actor=payload.measured_by,
                at=observed_at,
                details={
                    "observation_id": observation.id,
                    "temperature_c": payload.temperature_c,
                    "verdict": verdict_value,
                    "temp_min_c": batch.temp_min_c,
                    "temp_max_c": batch.temp_max_c,
                },
                note=payload.note,
            )
            batch_id = batch.id
    finally:
        if local_lock:
            local_lock.release()
    return get_batch(db, batch_id, clock)


def _fresh_code(db: Session, signing_key: str) -> tuple[str, str]:
    for _ in range(20):
        code = generate_code()
        digest = code_digest(code, signing_key)
        if not db.scalar(select(Handoff.id).where(Handoff.code_digest == digest)):
            return code, digest
    raise DomainError(
        503,
        "CODE_SPACE_BUSY",
        "Could not allocate a receipt code; retry shortly.",
        retryable=True,
    )


def _create_handoff_row(
    db: Session,
    *,
    batch: Batch,
    container: Container,
    source: Location,
    destination: Location,
    actor: str,
    ttl_minutes: int,
    now: datetime,
    signing_key: str,
) -> tuple[Handoff, str]:
    code, digest = _fresh_code(db, signing_key)
    handoff = Handoff(
        batch_id=batch.id,
        container_id=container.id,
        from_location_id=source.id,
        to_location_id=destination.id,
        code_digest=digest,
        created_by=actor,
        created_at=now,
        expires_at=now + timedelta(minutes=ttl_minutes),
    )
    db.add(handoff)
    db.flush()
    # A handoff from cold storage represents physical removal at initiation;
    # receiver lateness therefore contributes to the exposure budget.
    if source.is_cold_storage and not destination.is_cold_storage and container.out_since is None:
        container.out_since = now
        container.updated_at = now
    _event(
        db,
        batch_id=batch.id,
        container_id=container.id,
        handoff_id=handoff.id,
        event_type="handoff_initiated",
        actor=actor,
        at=now,
        details={
            "from": source.code,
            "to": destination.code,
            "expires_at": handoff.expires_at.isoformat(),
        },
    )
    return handoff, code


def create_handoff(
    db: Session, payload: HandoffCreate, clock: Clock, settings: Settings
) -> HandoffRead:
    now = clock.now()
    with db.begin():
        container = db.scalar(
            select(Container).where(Container.id == payload.container_id).with_for_update()
        )
        if container is None:
            raise DomainError(404, "CONTAINER_NOT_FOUND", "Container does not exist.")
        if container.status == ContainerStatus.REPLACED:
            raise DomainError(
                409,
                "CONTAINER_ALREADY_REPLACED",
                "This container was sealed after transloading; use the replacement container.",
                details={"replacement_container_id": container.replacement_container_id},
            )
        batch = db.scalar(select(Batch).where(Batch.id == container.batch_id).with_for_update())
        assert batch is not None
        if _has_unresolved_anomaly(db, batch.id, now):
            raise DomainError(
                409,
                "UNRESOLVED_ANOMALY",
                "Resolve or reopen the batch anomaly before another handoff.",
            )
        if batch.disposition != BatchDisposition.ACTIVE:
            raise DomainError(
                409,
                "BATCH_NOT_ACTIVE",
                "The batch must be active before it can move.",
                details={"disposition": batch.disposition.value},
            )
        pending = db.scalar(
            select(Handoff.id).where(
                Handoff.container_id == container.id, Handoff.status == HandoffStatus.PENDING
            )
        )
        if pending:
            raise DomainError(
                409, "HANDOFF_ALREADY_PENDING", "This container already has a handoff."
            )
        source = _location_by_code(db, payload.from_location_code.upper())
        destination = _location_by_code(db, payload.to_location_code.upper())
        if source.id == destination.id:
            raise DomainError(422, "SAME_LOCATION", "Source and destination must differ.")
        if container.current_location_id != source.id:
            raise DomainError(
                409,
                "LOCATION_MISMATCH",
                "The recorded container location does not match the requested source.",
                details={"recorded_location_id": container.current_location_id},
            )
        handoff, code = _create_handoff_row(
            db,
            batch=batch,
            container=container,
            source=source,
            destination=destination,
            actor=payload.created_by,
            ttl_minutes=payload.ttl_minutes or settings.handoff_default_ttl_minutes,
            now=now,
            signing_key=settings.handoff_signing_key,
        )
    return get_handoff(db, handoff.id, clock, receipt_code=code)


def _handoff_query(handoff_id: str):
    return (
        select(Handoff)
        .where(Handoff.id == handoff_id)
        .options(
            selectinload(Handoff.container).selectinload(Container.current_location),
            selectinload(Handoff.from_location),
            selectinload(Handoff.to_location),
        )
    )


def get_handoff(
    db: Session,
    handoff_id: str,
    clock: Clock,
    *,
    receipt_code: str | None = None,
    replayed: bool = False,
) -> HandoffRead:
    handoff = db.scalar(_handoff_query(handoff_id))
    if handoff is None:
        raise DomainError(404, "HANDOFF_NOT_FOUND", "Handoff does not exist.")
    batch = db.get(Batch, handoff.batch_id)
    assert batch is not None
    return _handoff_read(handoff, batch, clock.now(), receipt_code=receipt_code, replayed=replayed)


def list_handoffs(db: Session, clock: Clock, status: str | None = None) -> list[HandoffRead]:
    now = clock.now()
    handoffs = list(
        db.scalars(
            select(Handoff)
            .options(
                selectinload(Handoff.container).selectinload(Container.current_location),
                selectinload(Handoff.from_location),
                selectinload(Handoff.to_location),
            )
            .order_by(Handoff.created_at.desc())
        )
    )
    result: list[HandoffRead] = []
    for handoff in handoffs:
        batch = db.get(Batch, handoff.batch_id)
        assert batch is not None
        item = _handoff_read(handoff, batch, now)
        if status is None or item.status.value == status:
            result.append(item)
    return result


def _mark_anomaly(db: Session, handoff: Handoff, now: datetime, actor: str, reason: str) -> None:
    handoff.status = HandoffStatus.ANOMALY
    handoff.anomaly_at = now
    handoff.anomaly_reason = reason
    _event(
        db,
        batch_id=handoff.batch_id,
        container_id=handoff.container_id,
        handoff_id=handoff.id,
        event_type="handoff_anomaly",
        actor=actor,
        at=now,
        details={"reason": reason},
    )


def _apply_location_move(container: Container, destination: Location, now: datetime) -> None:
    source = container.current_location
    if not source.is_cold_storage and destination.is_cold_storage and container.out_since:
        container.accumulated_out_seconds += _seconds(container.out_since, now)
        container.out_since = None
    elif source.is_cold_storage and not destination.is_cold_storage:
        container.out_since = container.out_since or now
    container.current_location_id = destination.id
    container.current_location = destination
    container.updated_at = now


def _settle_return_to_source(
    container: Container, source: Location, destination: Location, now: datetime
) -> None:
    # A rejected or cancelled cold-to-warm handoff sends the container back to
    # its cold source; settle the out-of-storage segment that began at initiation.
    if source.is_cold_storage and not destination.is_cold_storage and container.out_since:
        container.accumulated_out_seconds += _seconds(container.out_since, now)
        container.out_since = None
        container.updated_at = now


def _code_action_lock(db: Session, digest: str) -> threading.Lock | None:
    if db.bind is None or db.bind.dialect.name != "sqlite":
        return None
    with _sqlite_locks_guard:
        return _sqlite_code_action_locks.setdefault(digest, threading.Lock())


def confirm_handoff(
    db: Session, payload: ConfirmRequest, clock: Clock, settings: Settings
) -> HandoffRead:
    digest = code_digest(payload.code, settings.handoff_signing_key)
    local_lock = _code_action_lock(db, digest)
    if local_lock:
        local_lock.acquire()
    try:
        deferred_error: DomainError | None = None
        replayed = False
        handoff_id: str | None = None
        with db.begin():
            handoff = db.scalar(
                select(Handoff).where(Handoff.code_digest == digest).with_for_update()
            )
            if handoff is None:
                raise DomainError(404, "INVALID_RECEIPT_CODE", "Receipt code was not found.")
            now = clock.now()
            handoff_id = handoff.id
            if handoff.status == HandoffStatus.RECEIVED:
                replayed = True
            elif handoff.status == HandoffStatus.CANCELLED:
                raise DomainError(409, "HANDOFF_CANCELLED", "This handoff was cancelled.")
            elif (
                handoff.status == HandoffStatus.ANOMALY
                and handoff.anomaly_reason == "RECEIVER_REJECTED"
            ):
                raise DomainError(
                    409,
                    "HANDOFF_REJECTED",
                    "The receiving shift rejected this handoff; reopen it to issue a new code.",
                )
            elif handoff.status == HandoffStatus.ANOMALY:
                raise DomainError(
                    409,
                    "HANDOFF_ANOMALY",
                    "This handoff is anomalous and must be resolved or reopened.",
                )
            elif _aware(handoff.expires_at) <= now:
                _mark_anomaly(db, handoff, now, "system", "HANDOFF_EXPIRED")
                deferred_error = DomainError(
                    410,
                    "HANDOFF_EXPIRED",
                    "The server-side receipt deadline has passed; reopen the handoff.",
                )
            else:
                container = db.scalar(
                    select(Container).where(Container.id == handoff.container_id).with_for_update()
                )
                assert container is not None
                batch = db.scalar(
                    select(Batch).where(Batch.id == handoff.batch_id).with_for_update()
                )
                assert batch is not None
                source = db.get(Location, handoff.from_location_id)
                destination = db.get(Location, handoff.to_location_id)
                assert source is not None and destination is not None
                if container.current_location_id != handoff.from_location_id:
                    _mark_anomaly(db, handoff, now, "system", "LOCATION_MISMATCH")
                    deferred_error = DomainError(
                        409,
                        "LOCATION_MISMATCH",
                        "The container is no longer at the recorded source.",
                    )
                else:
                    _, exposure = _exposure(container, now)
                    if exposure >= batch.max_out_minutes * 60:
                        _mark_anomaly(db, handoff, now, "system", "EXPOSURE_LIMIT_EXCEEDED")
                        deferred_error = DomainError(
                            409,
                            "EXPOSURE_LIMIT_EXCEEDED",
                            "Maximum out-of-cold-storage time was reached.",
                            details={
                                "exposure_seconds": exposure,
                                "limit_seconds": batch.max_out_minutes * 60,
                            },
                        )
                    else:
                        _apply_location_move(container, destination, now)
                        handoff.status = HandoffStatus.RECEIVED
                        handoff.received_by = payload.received_by
                        handoff.received_at = now
                        _event(
                            db,
                            batch_id=batch.id,
                            container_id=container.id,
                            handoff_id=handoff.id,
                            event_type="handoff_received",
                            actor=payload.received_by,
                            at=now,
                            details={"from": source.code, "to": destination.code},
                        )
        if deferred_error:
            raise deferred_error
        assert handoff_id is not None
        return get_handoff(db, handoff_id, clock, replayed=replayed)
    finally:
        if local_lock:
            local_lock.release()


def reject_handoff(
    db: Session, payload: RejectRequest, clock: Clock, settings: Settings
) -> HandoffRead:
    digest = code_digest(payload.code, settings.handoff_signing_key)
    local_lock = _code_action_lock(db, digest)
    if local_lock:
        local_lock.acquire()
    try:
        deferred_error: DomainError | None = None
        replayed = False
        handoff_id: str | None = None
        with db.begin():
            handoff = db.scalar(
                select(Handoff).where(Handoff.code_digest == digest).with_for_update()
            )
            if handoff is None:
                raise DomainError(404, "INVALID_RECEIPT_CODE", "Receipt code was not found.")
            now = clock.now()
            handoff_id = handoff.id
            if handoff.status == HandoffStatus.RECEIVED:
                raise DomainError(
                    409,
                    "HANDOFF_ALREADY_RECEIVED",
                    "This handoff was already received; a rejection can no longer win.",
                )
            if handoff.status == HandoffStatus.CANCELLED:
                raise DomainError(409, "HANDOFF_CANCELLED", "This handoff was cancelled.")
            if (
                handoff.status == HandoffStatus.ANOMALY
                and handoff.anomaly_reason == "RECEIVER_REJECTED"
            ):
                # Idempotent replay: repeat submissions return the first rejection fact.
                replayed = True
            elif handoff.status == HandoffStatus.ANOMALY:
                raise DomainError(
                    409,
                    "HANDOFF_ANOMALY",
                    "This handoff is anomalous and must be resolved or reopened.",
                )
            elif _aware(handoff.expires_at) <= now:
                _mark_anomaly(db, handoff, now, "system", "HANDOFF_EXPIRED")
                deferred_error = DomainError(
                    410,
                    "HANDOFF_EXPIRED",
                    "The server-side receipt deadline has passed; reopen the handoff.",
                )
            else:
                # Same adjudication order as confirmation: handoff, container, batch.
                container = db.scalar(
                    select(Container).where(Container.id == handoff.container_id).with_for_update()
                )
                batch = db.scalar(
                    select(Batch).where(Batch.id == handoff.batch_id).with_for_update()
                )
                source = db.get(Location, handoff.from_location_id)
                destination = db.get(Location, handoff.to_location_id)
                assert container is not None and batch is not None
                assert source is not None and destination is not None
                if container.current_location_id != handoff.from_location_id:
                    _mark_anomaly(db, handoff, now, "system", "LOCATION_MISMATCH")
                    deferred_error = DomainError(
                        409,
                        "LOCATION_MISMATCH",
                        "The container is no longer at the recorded source.",
                    )
                else:
                    handoff.status = HandoffStatus.ANOMALY
                    handoff.anomaly_at = now
                    handoff.anomaly_reason = "RECEIVER_REJECTED"
                    handoff.rejected_by = payload.rejected_by
                    handoff.rejected_at = now
                    handoff.reject_reason = payload.reason
                    handoff.reject_note = payload.note
                    batch.disposition = BatchDisposition.REVIEW
                    # The container stays at its source; a cold-to-warm removal is
                    # settled as a return so the exposure segment stops accruing.
                    _settle_return_to_source(container, source, destination, now)
                    _event(
                        db,
                        batch_id=batch.id,
                        container_id=container.id,
                        handoff_id=handoff.id,
                        event_type="handoff_rejected",
                        actor=payload.rejected_by,
                        at=now,
                        details={
                            "reason": payload.reason,
                            "from": source.code,
                            "to": destination.code,
                        },
                        note=payload.note,
                    )
        if deferred_error:
            raise deferred_error
        assert handoff_id is not None
        return get_handoff(db, handoff_id, clock, replayed=replayed)
    finally:
        if local_lock:
            local_lock.release()


def cancel_handoff(
    db: Session, handoff_id: str, payload: CancelRequest, clock: Clock
) -> HandoffRead:
    deferred_error: DomainError | None = None
    with db.begin():
        handoff = db.scalar(select(Handoff).where(Handoff.id == handoff_id).with_for_update())
        if handoff is None:
            raise DomainError(404, "HANDOFF_NOT_FOUND", "Handoff does not exist.")
        now = clock.now()
        if handoff.created_by != payload.actor:
            raise DomainError(403, "NOT_HANDOFF_CREATOR", "Only the initiator may cancel.")
        if handoff.status == HandoffStatus.CANCELLED:
            handoff.cancelled_by = handoff.cancelled_by or payload.actor
        elif handoff.status != HandoffStatus.PENDING:
            raise DomainError(
                409, "HANDOFF_NOT_CANCELLABLE", "Only pending handoffs can be cancelled."
            )
        elif _aware(handoff.expires_at) <= now:
            _mark_anomaly(db, handoff, now, "system", "HANDOFF_EXPIRED")
            deferred_error = DomainError(
                409, "HANDOFF_EXPIRED", "Expired handoffs must be reopened or resolved."
            )
        else:
            handoff.status = HandoffStatus.CANCELLED
            handoff.cancelled_at = now
            handoff.cancelled_by = payload.actor
            container = db.scalar(
                select(Container).where(Container.id == handoff.container_id).with_for_update()
            )
            source = db.get(Location, handoff.from_location_id)
            destination = db.get(Location, handoff.to_location_id)
            assert container is not None and source is not None and destination is not None
            _settle_return_to_source(container, source, destination, now)
            _event(
                db,
                batch_id=handoff.batch_id,
                container_id=handoff.container_id,
                handoff_id=handoff.id,
                event_type="handoff_cancelled",
                actor=payload.actor,
                at=now,
            )
    if deferred_error:
        raise deferred_error
    return get_handoff(db, handoff_id, clock)


def mark_anomaly(
    db: Session, handoff_id: str, payload: MarkAnomalyRequest, clock: Clock
) -> HandoffRead:
    with db.begin():
        handoff = db.scalar(select(Handoff).where(Handoff.id == handoff_id).with_for_update())
        if handoff is None:
            raise DomainError(404, "HANDOFF_NOT_FOUND", "Handoff does not exist.")
        now = clock.now()
        if handoff.status == HandoffStatus.ANOMALY:
            handoff.anomaly_reason = handoff.anomaly_reason or payload.reason
        elif handoff.status != HandoffStatus.PENDING:
            raise DomainError(
                409, "HANDOFF_NOT_PENDING", "Only pending handoffs can become anomalous."
            )
        elif _aware(handoff.expires_at) > now:
            raise DomainError(409, "HANDOFF_NOT_EXPIRED", "The handoff deadline has not passed.")
        else:
            _mark_anomaly(db, handoff, now, payload.actor, payload.reason)
    return get_handoff(db, handoff_id, clock)


def resolve_anomaly(
    db: Session, handoff_id: str, payload: ResolveAnomalyRequest, clock: Clock
) -> HandoffRead:
    disposition = {
        "isolate": BatchDisposition.ISOLATED,
        "review": BatchDisposition.REVIEW,
        "release": BatchDisposition.ACTIVE,
    }[payload.decision]
    with db.begin():
        handoff = db.scalar(select(Handoff).where(Handoff.id == handoff_id).with_for_update())
        if handoff is None:
            raise DomainError(404, "HANDOFF_NOT_FOUND", "Handoff does not exist.")
        now = clock.now()
        if handoff.status != HandoffStatus.ANOMALY:
            raise DomainError(409, "HANDOFF_NOT_ANOMALOUS", "This handoff has no anomaly.")
        batch = db.scalar(select(Batch).where(Batch.id == handoff.batch_id).with_for_update())
        assert batch is not None
        if handoff.resolved_at is None:
            handoff.resolved_at = now
            handoff.resolution = payload.decision.upper()
            batch.disposition = disposition
            _event(
                db,
                batch_id=batch.id,
                container_id=handoff.container_id,
                handoff_id=handoff.id,
                event_type="anomaly_resolved",
                actor=payload.actor,
                at=now,
                details={"decision": payload.decision},
                note=payload.note,
            )
    return get_handoff(db, handoff_id, clock)


def reopen_handoff(
    db: Session,
    handoff_id: str,
    payload: ReopenRequest,
    clock: Clock,
    settings: Settings,
) -> HandoffRead:
    with db.begin():
        old = db.scalar(select(Handoff).where(Handoff.id == handoff_id).with_for_update())
        if old is None:
            raise DomainError(404, "HANDOFF_NOT_FOUND", "Handoff does not exist.")
        now = clock.now()
        if old.status == HandoffStatus.PENDING and _aware(old.expires_at) <= now:
            _mark_anomaly(db, old, now, "system", "HANDOFF_EXPIRED")
        if old.status != HandoffStatus.ANOMALY:
            raise DomainError(
                409, "HANDOFF_NOT_ANOMALOUS", "Only anomalous handoffs can be reopened."
            )
        if old.successor_id:
            raise DomainError(
                409,
                "HANDOFF_ALREADY_REOPENED",
                "This handoff already has a successor.",
                details={"successor_id": old.successor_id},
            )
        other_anomaly = db.scalar(
            select(Handoff.id).where(
                Handoff.batch_id == old.batch_id,
                Handoff.id != old.id,
                Handoff.status == HandoffStatus.ANOMALY,
                Handoff.resolved_at.is_(None),
            )
        )
        if other_anomaly:
            raise DomainError(
                409, "UNRESOLVED_ANOMALY", "Another anomaly on this batch must be resolved first."
            )
        container = db.scalar(
            select(Container).where(Container.id == old.container_id).with_for_update()
        )
        batch = db.scalar(select(Batch).where(Batch.id == old.batch_id).with_for_update())
        source = db.get(Location, old.from_location_id)
        destination = db.get(Location, old.to_location_id)
        assert container and batch and source and destination
        if container.current_location_id != source.id:
            raise DomainError(409, "LOCATION_MISMATCH", "Container is no longer at the source.")
        batch.disposition = BatchDisposition.ACTIVE
        old.resolved_at = now
        old.resolution = "REOPENED"
        new, code = _create_handoff_row(
            db,
            batch=batch,
            container=container,
            source=source,
            destination=destination,
            actor=payload.actor,
            ttl_minutes=payload.ttl_minutes or settings.handoff_default_ttl_minutes,
            now=now,
            signing_key=settings.handoff_signing_key,
        )
        old.successor_id = new.id
        _event(
            db,
            batch_id=batch.id,
            container_id=container.id,
            handoff_id=old.id,
            event_type="handoff_reopened",
            actor=payload.actor,
            at=now,
            details={"successor_id": new.id},
        )
    return get_handoff(db, new.id, clock, receipt_code=code)


def _inventory_lock(db: Session, key: str) -> threading.Lock | None:
    # SELECT ... FOR UPDATE serialises recounts against handoffs on PostgreSQL.
    # SQLite ignores row locks, so mirror the per-key process lock used elsewhere.
    if db.bind is None or db.bind.dialect.name != "sqlite":
        return None
    with _sqlite_locks_guard:
        return _sqlite_code_action_locks.setdefault(f"inventory:{key}", threading.Lock())


_INVENTORY_CATEGORY_ORDER = {
    InventoryCheckCategory.MATCHED: 0,
    InventoryCheckCategory.MISSING: 1,
    InventoryCheckCategory.MISPLACED: 2,
    InventoryCheckCategory.UNKNOWN: 3,
}


def _inventory_summary(check: LocationInventoryCheck) -> InventoryCheckSummary:
    return InventoryCheckSummary(
        id=check.id,
        location_id=check.location_id,
        checked_by=check.checked_by,
        created_at=check.created_at,
        matched_count=check.matched_count,
        missing_count=check.missing_count,
        misplaced_count=check.misplaced_count,
        unknown_count=check.unknown_count,
        scanned_count=check.scanned_count,
        location=LocationRead.model_validate(check.location),
    )


def _recent_check_summaries(
    db: Session,
    location_id: str,
    *,
    before_sequence: int | None = None,
) -> list[LocationInventoryCheck]:
    statement = (
        select(LocationInventoryCheck)
        .where(LocationInventoryCheck.location_id == location_id)
        .options(selectinload(LocationInventoryCheck.location))
        .order_by(LocationInventoryCheck.sequence_number.desc())
        .limit(10)
    )
    if before_sequence is not None:
        # Only records already visible at submission time, so re-reading an
        # immutable check never surfaces checks created afterwards. Sequence is
        # total per location even when several checks share one created_at.
        statement = statement.where(
            LocationInventoryCheck.sequence_number < before_sequence
        )
    return list(db.scalars(statement))


def create_inventory_check(
    db: Session, location_id: str, payload: InventoryCheckRequest, clock: Clock
) -> InventoryCheckRead:
    """Persist one immutable cold-location recount.

    The location row and the containers recorded there are locked inside the
    same transaction that snapshots and classifies them, so a concurrent
    handoff cannot move a container between the snapshot and the verdict. The
    check only forms evidence: no container, batch, handoff or timeline row is
    modified.
    """
    # Payload verification happens before the transaction opens, so a rejected
    # submission (blank checker or blank/duplicate labels) never touches the
    # database.
    checked_by = payload.checked_by.strip()
    if not checked_by:
        raise DomainError(
            422,
            "INVALID_INVENTORY_CHECKED_BY",
            "checked_by must name the person performing the recount.",
        )
    labels = [label.strip() for label in payload.labels]
    folded_counts = Counter(label.casefold() for label in labels)
    if any(not label for label in labels) or any(count > 1 for count in folded_counts.values()):
        raise DomainError(
            422,
            "INVALID_INVENTORY_LABELS",
            "Labels must not be blank or duplicated within one recount.",
            details={
                "blank": [index + 1 for index, label in enumerate(labels) if not label],
                "duplicated": sorted(
                    {label for label in labels if folded_counts[label.casefold()] > 1}
                ),
            },
        )

    local_lock = _inventory_lock(db, location_id)
    if local_lock:
        local_lock.acquire()
    check_id = ""
    try:
        now = clock.now()
        with db.begin():
            # Lock the location first; the handoff transactions lock container
            # then batch rows, and inventory never writes them, so the lock
            # orders do not create a write/write deadlock.
            location = db.scalar(
                select(Location).where(Location.id == location_id).with_for_update()
            )
            if location is None:
                raise DomainError(404, "LOCATION_NOT_FOUND", "Location does not exist.")
            if not location.is_cold_storage:
                raise DomainError(
                    409,
                    "LOCATION_NOT_COLD_STORAGE",
                    "Only cold-storage locations can be inventoried.",
                    details={"location_id": location.id, "code": location.code},
                )

            # Only containers still in circulation are part of the book snapshot.
            at_location = list(
                db.scalars(
                    select(Container)
                    .where(
                        Container.current_location_id == location.id,
                        Container.status == ContainerStatus.ACTIVE,
                    )
                    .with_for_update()
                    .order_by(Container.id)
                )
            )
            batch_ids = {container.batch_id for container in at_location}
            batches = {
                batch.id: batch
                for batch in db.scalars(select(Batch).where(Batch.id.in_(batch_ids)))
            }
            # The whole active container index resolves scanned labels: a label
            # found here is matched, elsewhere means misplaced here, nowhere
            # means unknown.
            all_active = list(
                db.scalars(
                    select(Container).where(Container.status == ContainerStatus.ACTIVE)
                )
            )
            by_folded_label: dict[str, list[Container]] = {}
            for container in all_active:
                by_folded_label.setdefault(container.label.casefold(), []).append(container)
            locations = {
                item.id: item for item in db.scalars(select(Location))
            }
            all_batch_ids = {container.batch_id for container in all_active}
            all_batches = {
                batch.id: batch
                for batch in db.scalars(select(Batch).where(Batch.id.in_(all_batch_ids)))
            }

            def make_item(category: InventoryCheckCategory, **fields) -> LocationInventoryCheckItem:
                return LocationInventoryCheckItem(category=category, **fields)

            rows: list[LocationInventoryCheckItem] = []

            # Containers the scan actually accounted for here. A scanned label
            # resolves to exactly one book container, so two book containers
            # sharing a label (allowed across batches) only clear one of them:
            # the other must remain a book-missing row even though the label was
            # scanned.
            matched_here_ids: set[str] = set()

            # Scanned labels, in scan order.
            for line, label in enumerate(labels, start=1):
                candidates = by_folded_label.get(label.casefold(), [])
                if not candidates:
                    rows.append(
                        make_item(
                            InventoryCheckCategory.UNKNOWN,
                            scanned_label=label,
                            line_number=line,
                        )
                    )
                    continue
                here = [c for c in candidates if c.current_location_id == location.id]
                # Deterministic tie-break if the same label exists in several
                # batches: prefer the one recorded at the scanned location.
                chosen = sorted(here or candidates, key=lambda c: c.id)[0]
                recorded = locations[chosen.current_location_id]
                batch = all_batches[chosen.batch_id]
                if chosen.current_location_id == location.id:
                    category = InventoryCheckCategory.MATCHED
                    matched_here_ids.add(chosen.id)
                else:
                    category = InventoryCheckCategory.MISPLACED
                rows.append(
                    make_item(
                        category,
                        scanned_label=label,
                        line_number=line,
                        container_id=chosen.id,
                        batch_id=chosen.batch_id,
                        accession_number=batch.accession_number,
                        container_label=chosen.label,
                        recorded_location_id=recorded.id,
                        recorded_location_code=recorded.code,
                        recorded_location_name=recorded.name,
                    )
                )

            # Book containers at this location that the scan did not list.
            missing_line = 0
            for container in at_location:
                if container.id in matched_here_ids:
                    continue
                missing_line += 1
                batch = batches[container.batch_id]
                rows.append(
                    make_item(
                        InventoryCheckCategory.MISSING,
                        scanned_label=None,
                        line_number=missing_line,
                        container_id=container.id,
                        batch_id=container.batch_id,
                        accession_number=batch.accession_number,
                        container_label=container.label,
                        recorded_location_id=location.id,
                        recorded_location_code=location.code,
                        recorded_location_name=location.name,
                    )
                )

            counts = {category: 0 for category in InventoryCheckCategory}
            for item in rows:
                counts[item.category] += 1

            # The location row is already locked, so the per-location sequence is
            # gapless and free of races even when created_at ties.
            last_sequence = db.scalar(
                select(func.max(LocationInventoryCheck.sequence_number)).where(
                    LocationInventoryCheck.location_id == location.id
                )
            )
            sequence_number = (last_sequence or 0) + 1

            check = LocationInventoryCheck(
                location_id=location.id,
                checked_by=checked_by,
                sequence_number=sequence_number,
                created_at=now,
                matched_count=counts[InventoryCheckCategory.MATCHED],
                missing_count=counts[InventoryCheckCategory.MISSING],
                misplaced_count=counts[InventoryCheckCategory.MISPLACED],
                unknown_count=counts[InventoryCheckCategory.UNKNOWN],
                scanned_count=len(labels),
            )
            db.add(check)
            db.flush()
            for item in rows:
                item.check_id = check.id
                db.add(item)
            db.flush()
            check_id = check.id
    finally:
        if local_lock:
            local_lock.release()
    return get_inventory_check(db, check_id)


def get_inventory_check(db: Session, check_id: str) -> InventoryCheckRead:
    check = db.scalar(
        select(LocationInventoryCheck)
        .where(LocationInventoryCheck.id == check_id)
        .options(
            selectinload(LocationInventoryCheck.location),
            selectinload(LocationInventoryCheck.items),
        )
    )
    if check is None:
        raise DomainError(404, "INVENTORY_CHECK_NOT_FOUND", "Inventory check does not exist.")
    recent = _recent_check_summaries(
        db, check.location_id, before_sequence=check.sequence_number
    )
    summaries = [_inventory_summary(item) for item in recent]
    return InventoryCheckRead(
        **_inventory_summary(check).model_dump(),
        items=sorted(
            check.items,
            key=lambda item: (_INVENTORY_CATEGORY_ORDER[item.category], item.line_number),
        ),
        recent_checks=summaries,
    )


def list_location_inventory_checks(
    db: Session, location_id: str
) -> list[InventoryCheckSummary]:
    location = db.scalar(select(Location).where(Location.id == location_id))
    if location is None:
        raise DomainError(404, "LOCATION_NOT_FOUND", "Location does not exist.")
    return [_inventory_summary(check) for check in _recent_check_summaries(db, location_id)]
