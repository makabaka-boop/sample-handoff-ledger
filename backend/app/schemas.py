from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .models import BatchDisposition, ContainerStatus, HandoffStatus, InventoryCheckCategory
from .temperature import (
    MAX_PLAUSIBLE_C,
    MIN_PLAUSIBLE_C,
    format_temperature_zone,
    parse_temperature_zone,
)


class LocationCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    is_cold_storage: bool


class LocationRead(LocationCreate):
    id: str

    model_config = {"from_attributes": True}


class InventoryCheckRequest(BaseModel):
    checked_by: str = Field(min_length=1, max_length=100)
    labels: list[str] = Field(min_length=1, max_length=1000)

    @field_validator("labels")
    @classmethod
    def trimmed_labels(cls, labels: list[str]) -> list[str]:
        return [label.strip() for label in labels]


class InventoryCheckItemRead(BaseModel):
    id: str
    category: InventoryCheckCategory
    scanned_label: str | None
    line_number: int
    container_id: str | None
    batch_id: str | None
    accession_number: str | None
    container_label: str | None
    recorded_location_id: str | None
    recorded_location_code: str | None
    recorded_location_name: str | None

    model_config = {"from_attributes": True}


class InventoryCheckSummary(BaseModel):
    id: str
    location_id: str
    checked_by: str
    created_at: datetime
    matched_count: int
    missing_count: int
    misplaced_count: int
    unknown_count: int
    scanned_count: int
    location: LocationRead


class InventoryCheckRead(InventoryCheckSummary):
    items: list[InventoryCheckItemRead]

    # The current check followed by the location's most recent earlier checks.
    recent_checks: list[InventoryCheckSummary]


class ContainerCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    initial_location_code: str = Field(min_length=1, max_length=40)


class BatchCreate(BaseModel):
    accession_number: str = Field(min_length=1, max_length=80)
    temperature_zone: str | None = Field(default=None, min_length=1, max_length=40)
    temp_min_c: float | None = Field(default=None, ge=MIN_PLAUSIBLE_C, le=MAX_PLAUSIBLE_C)
    temp_max_c: float | None = Field(default=None, ge=MIN_PLAUSIBLE_C, le=MAX_PLAUSIBLE_C)
    max_out_minutes: int = Field(ge=1, le=10080)
    created_by: str = Field(min_length=1, max_length=100)
    containers: list[ContainerCreate] = Field(min_length=1, max_length=100)

    @field_validator("containers")
    @classmethod
    def unique_labels(cls, containers: list[ContainerCreate]) -> list[ContainerCreate]:
        labels = [container.label.casefold() for container in containers]
        if len(labels) != len(set(labels)):
            raise ValueError("container labels must be unique within a batch")
        return containers

    @model_validator(mode="after")
    def resolve_temperature_bounds(self) -> "BatchCreate":
        if self.temp_min_c is not None and self.temp_max_c is not None:
            if self.temp_min_c > self.temp_max_c:
                raise ValueError("temp_min_c must not be above temp_max_c")
            if self.temperature_zone is None:
                self.temperature_zone = format_temperature_zone(
                    self.temp_min_c, self.temp_max_c
                )
            return self
        # Legacy clients only sent free text; parse it so every batch gets bounds.
        if self.temperature_zone:
            try:
                low, high = parse_temperature_zone(self.temperature_zone)
            except ValueError as exc:
                raise ValueError(
                    "temperature_zone must be a celsius range like '2–8°C' "
                    "or supply temp_min_c/temp_max_c"
                ) from exc
            self.temp_min_c, self.temp_max_c = low, high
            return self
        raise ValueError("supply temp_min_c and temp_max_c (or a parseable temperature_zone)")


class ContainerRead(BaseModel):
    id: str
    label: str
    current_location: LocationRead
    accumulated_out_seconds: int
    current_out_seconds: int
    total_out_seconds: int
    max_out_seconds: int
    exposure_exceeded: bool
    out_since: datetime | None
    updated_at: datetime
    status: ContainerStatus
    replacement_container_id: str | None
    replaced_by: str | None
    replaced_at: datetime | None
    replacement_reason: str | None


class ContainerReplaceRequest(BaseModel):
    new_label: str = Field(min_length=1, max_length=100)
    actor: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("note")
    @classmethod
    def empty_note_to_none(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value


class TemperatureObservationRequest(BaseModel):
    temperature_c: float = Field(ge=MIN_PLAUSIBLE_C, le=MAX_PLAUSIBLE_C)
    measured_by: str = Field(min_length=1, max_length=100)
    observed_at: datetime
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("note")
    @classmethod
    def blank_note_to_none(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value


class TemperatureObservationRead(BaseModel):
    id: str
    batch_id: str
    container_id: str
    temperature_c: float
    verdict: Literal["normal", "out_of_range"]
    measured_by: str
    observed_at: datetime
    note: str | None
    created_at: datetime
    temp_min_c: float
    temp_max_c: float

    model_config = {"from_attributes": True}


class BatchSummary(BaseModel):
    id: str
    accession_number: str
    temperature_zone: str
    temp_min_c: float
    temp_max_c: float
    max_out_minutes: int
    disposition: BatchDisposition
    created_at: datetime
    has_unresolved_anomaly: bool
    containers: list[ContainerRead]


class TimelineRead(BaseModel):
    id: str
    container_id: str | None
    handoff_id: str | None
    event_type: str
    actor: str
    occurred_at: datetime
    details: dict[str, Any]
    note: str | None


class BatchDetail(BatchSummary):
    timeline: list[TimelineRead]
    temperature_observations: list[TemperatureObservationRead]


class HandoffCreate(BaseModel):
    container_id: str
    from_location_code: str
    to_location_code: str
    created_by: str = Field(min_length=1, max_length=100)
    ttl_minutes: int | None = Field(default=None, ge=1, le=1440)

    @model_validator(mode="after")
    def different_locations(self) -> "HandoffCreate":
        if self.from_location_code == self.to_location_code:
            raise ValueError("source and destination must differ")
        return self


class ConfirmRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    received_by: str = Field(min_length=1, max_length=100)


RejectReason = Literal[
    "seal_broken",
    "label_mismatch",
    "package_contaminated",
    "other",
]


class RejectRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    rejected_by: str = Field(min_length=1, max_length=100)
    reason: RejectReason
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("note")
    @classmethod
    def empty_note_to_none(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value


class CancelRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=100)


class RerouteRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=100)
    to_location_code: str = Field(min_length=1, max_length=40)
    reason: str = Field(min_length=1, max_length=200)


class MarkAnomalyRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=100)
    reason: str = Field(default="HANDOFF_EXPIRED", min_length=1, max_length=80)


class ResolveAnomalyRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=100)
    decision: Literal["isolate", "review", "release"]
    note: str = Field(min_length=1, max_length=2000)


class ReopenRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=100)
    ttl_minutes: int | None = Field(default=None, ge=1, le=1440)


class HandoffRead(BaseModel):
    id: str
    batch_id: str
    accession_number: str
    container_id: str
    container_label: str
    from_location: LocationRead
    to_location: LocationRead
    original_to_location: LocationRead | None = None
    rerouted_by: str | None = None
    rerouted_at: datetime | None = None
    reroute_reason: str | None = None
    created_by: str
    received_by: str | None
    cancelled_by: str | None
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    reject_reason: str | None = None
    reject_note: str | None = None
    status: HandoffStatus
    persisted_status: HandoffStatus
    created_at: datetime
    expires_at: datetime
    received_at: datetime | None
    cancelled_at: datetime | None
    anomaly_at: datetime | None
    anomaly_reason: str | None
    resolved_at: datetime | None
    resolution: str | None
    successor_id: str | None
    remaining_seconds: int
    server_time: datetime
    exposure_seconds: int
    exposure_limit_seconds: int
    exposure_exceeded: bool
    receipt_code: str | None = None
    replayed: bool = False


class HealthRead(BaseModel):
    status: Literal["ok"]
    database: Literal["ok"]
