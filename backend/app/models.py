import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def uuid_str() -> str:
    return str(uuid.uuid4())


class HandoffStatus(str, enum.Enum):
    PENDING = "pending"
    RECEIVED = "received"
    CANCELLED = "cancelled"
    ANOMALY = "anomaly"


class BatchDisposition(str, enum.Enum):
    ACTIVE = "active"
    ISOLATED = "isolated"
    REVIEW = "review"
    RELEASED = "released"


class ContainerStatus(str, enum.Enum):
    ACTIVE = "active"
    REPLACED = "replaced"


class TemperatureVerdict(str, enum.Enum):
    NORMAL = "normal"
    OUT_OF_RANGE = "out_of_range"


class InventoryCheckCategory(str, enum.Enum):
    MATCHED = "matched"
    MISSING = "missing"
    MISPLACED = "misplaced"
    UNKNOWN = "unknown"


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_cold_storage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    accession_number: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    temperature_zone: Mapped[str] = mapped_column(String(40), nullable=False)
    temp_min_c: Mapped[float] = mapped_column(Float, nullable=False)
    temp_max_c: Mapped[float] = mapped_column(Float, nullable=False)
    max_out_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    disposition: Mapped[BatchDisposition] = mapped_column(
        Enum(BatchDisposition, native_enum=False), default=BatchDisposition.ACTIVE, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    containers: Mapped[list["Container"]] = relationship(back_populates="batch")
    temperature_observations: Mapped[list["TemperatureObservation"]] = relationship(
        back_populates="batch"
    )

    __table_args__ = (CheckConstraint("temp_min_c <= temp_max_c", name="ck_batch_temp_bounds"),)


class Container(Base):
    __tablename__ = "containers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    current_location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), nullable=False)
    accumulated_out_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    out_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[ContainerStatus] = mapped_column(
        Enum(ContainerStatus, native_enum=False),
        default=ContainerStatus.ACTIVE,
        nullable=False,
    )
    replacement_container_id: Mapped[str | None] = mapped_column(
        ForeignKey("containers.id"), nullable=True
    )
    replaced_by: Mapped[str | None] = mapped_column(String(100))
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replacement_reason: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    batch: Mapped[Batch] = relationship(back_populates="containers")
    current_location: Mapped[Location] = relationship(foreign_keys=[current_location_id])
    __table_args__ = (Index("uq_container_label_per_batch", "batch_id", "label", unique=True),)


class Handoff(Base):
    __tablename__ = "handoffs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), nullable=False, index=True)
    container_id: Mapped[str] = mapped_column(
        ForeignKey("containers.id"), nullable=False, index=True
    )
    from_location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), nullable=False)
    to_location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), nullable=False)
    # Reroute provenance; all NULL for handoffs that were never rerouted.
    original_to_location_id: Mapped[str | None] = mapped_column(
        ForeignKey("locations.id"), nullable=True
    )
    rerouted_by: Mapped[str | None] = mapped_column(String(100))
    rerouted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reroute_reason: Mapped[str | None] = mapped_column(String(200))
    code_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    received_by: Mapped[str | None] = mapped_column(String(100))
    cancelled_by: Mapped[str | None] = mapped_column(String(100))
    rejected_by: Mapped[str | None] = mapped_column(String(100))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(String(40))
    reject_note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[HandoffStatus] = mapped_column(
        Enum(HandoffStatus, native_enum=False), default=HandoffStatus.PENDING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anomaly_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anomaly_reason: Mapped[str | None] = mapped_column(String(80))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(String(80))
    successor_id: Mapped[str | None] = mapped_column(ForeignKey("handoffs.id"))

    container: Mapped[Container] = relationship(foreign_keys=[container_id])
    from_location: Mapped[Location] = relationship(foreign_keys=[from_location_id])
    to_location: Mapped[Location] = relationship(foreign_keys=[to_location_id])
    original_to_location: Mapped[Location | None] = relationship(
        foreign_keys=[original_to_location_id]
    )


class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), nullable=False, index=True)
    container_id: Mapped[str | None] = mapped_column(ForeignKey("containers.id"), index=True)
    handoff_id: Mapped[str | None] = mapped_column(ForeignKey("handoffs.id"), index=True)
    observation_id: Mapped[str | None] = mapped_column(
        ForeignKey("temperature_observations.id"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("uq_timeline_event_per_observation", "observation_id", unique=True),
    )


class TemperatureObservation(Base):
    """One manually measured celsius reading with its batch-bound verdict."""

    __tablename__ = "temperature_observations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), nullable=False, index=True)
    container_id: Mapped[str] = mapped_column(
        ForeignKey("containers.id"), nullable=False, index=True
    )
    temperature_c: Mapped[float] = mapped_column(Float, nullable=False)
    verdict: Mapped[TemperatureVerdict] = mapped_column(
        Enum(TemperatureVerdict, native_enum=False), nullable=False
    )
    measured_by: Mapped[str] = mapped_column(String(100), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    batch: Mapped[Batch] = relationship(back_populates="temperature_observations")
    container: Mapped[Container] = relationship(foreign_keys=[container_id])


class LocationInventoryCheck(Base):
    """An immutable cold-location recount: scanned labels compared with the
    server-side container snapshot taken in the same transaction. The check is
    evidence only — it never moves containers or changes batch disposition."""

    __tablename__ = "location_inventory_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    location_id: Mapped[str] = mapped_column(
        ForeignKey("locations.id"), nullable=False, index=True
    )
    checked_by: Mapped[str] = mapped_column(String(100), nullable=False)
    # Gapless, per-location submission order. created_at alone cannot order checks
    # whose server clock returns the same instant (or freezes): the sequence is
    # assigned while the location row is locked and makes "before/after" total.
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False)
    missing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    misplaced_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unknown_count: Mapped[int] = mapped_column(Integer, nullable=False)
    scanned_count: Mapped[int] = mapped_column(Integer, nullable=False)

    location: Mapped[Location] = relationship()
    items: Mapped[list["LocationInventoryCheckItem"]] = relationship(
        back_populates="check", cascade="all"
    )

    __table_args__ = (
        Index(
            "uq_inventory_check_location_sequence",
            "location_id",
            "sequence_number",
            unique=True,
        ),
    )


class LocationInventoryCheckItem(Base):
    """One immutable classified row of a recount. Snapshots are denormalised so
    later moves, transloads or renames never rewrite the evidence."""

    __tablename__ = "location_inventory_check_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    check_id: Mapped[str] = mapped_column(
        ForeignKey("location_inventory_checks.id"), nullable=False, index=True
    )
    category: Mapped[InventoryCheckCategory] = mapped_column(
        Enum(InventoryCheckCategory, native_enum=False), nullable=False
    )
    scanned_label: Mapped[str | None] = mapped_column(String(100))
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    container_id: Mapped[str | None] = mapped_column(String(36))
    batch_id: Mapped[str | None] = mapped_column(String(36))
    accession_number: Mapped[str | None] = mapped_column(String(80))
    container_label: Mapped[str | None] = mapped_column(String(100))
    recorded_location_id: Mapped[str | None] = mapped_column(String(36))
    recorded_location_code: Mapped[str | None] = mapped_column(String(40))
    recorded_location_name: Mapped[str | None] = mapped_column(String(100))

    check: Mapped[LocationInventoryCheck] = relationship(back_populates="items")

    __table_args__ = (
        Index(
            "uq_inventory_check_category_line",
            "check_id",
            "category",
            "line_number",
            unique=True,
        ),
    )
