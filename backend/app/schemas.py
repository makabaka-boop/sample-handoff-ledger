from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .models import BatchDisposition, HandoffStatus


class LocationCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    is_cold_storage: bool


class LocationRead(LocationCreate):
    id: str

    model_config = {"from_attributes": True}


class ContainerCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    initial_location_code: str = Field(min_length=1, max_length=40)


class BatchCreate(BaseModel):
    accession_number: str = Field(min_length=1, max_length=80)
    temperature_zone: str = Field(min_length=1, max_length=40)
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


class BatchSummary(BaseModel):
    id: str
    accession_number: str
    temperature_zone: str
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


class CancelRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=100)


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
    created_by: str
    received_by: str | None
    cancelled_by: str | None
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
