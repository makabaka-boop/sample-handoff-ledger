import type {
  Batch,
  Container,
  Handoff,
  InventoryCheckItem,
  InventoryCheckResult,
  Location,
  TemperatureObservation,
} from "../types";

let sequence = 0;

function uid(prefix: string): string {
  sequence += 1;
  return `${prefix}-${sequence}`;
}

export function makeLocation(overrides: Partial<Location> = {}): Location {
  return {
    id: uid("loc"),
    code: "FRIDGE",
    name: "冷藏冰箱",
    is_cold_storage: true,
    ...overrides,
  };
}

export function makeContainer(overrides: Partial<Container> = {}): Container {
  return {
    id: uid("container"),
    label: "TUBE-A",
    current_location: makeLocation(),
    accumulated_out_seconds: 0,
    current_out_seconds: 0,
    total_out_seconds: 0,
    max_out_seconds: 1800,
    exposure_exceeded: false,
    out_since: null,
    updated_at: "2026-09-10T08:00:00Z",
    status: "active",
    replacement_container_id: null,
    replaced_by: null,
    replaced_at: null,
    replacement_reason: null,
    ...overrides,
  };
}

export function makeBatch(overrides: Partial<Batch> = {}): Batch {
  return {
    id: uid("batch"),
    accession_number: "BATCH-001",
    temperature_zone: "2–8°C",
    temp_min_c: 2,
    temp_max_c: 8,
    max_out_minutes: 30,
    disposition: "active",
    created_at: "2026-09-10T08:00:00Z",
    has_unresolved_anomaly: false,
    containers: [makeContainer()],
    timeline: [],
    temperature_observations: [],
    ...overrides,
  };
}

export function makeObservation(
  batchId: string,
  containerId: string,
  overrides: Partial<TemperatureObservation> = {},
): TemperatureObservation {
  return {
    id: uid("observation"),
    batch_id: batchId,
    container_id: containerId,
    temperature_c: 5,
    verdict: "normal",
    measured_by: "night-a",
    observed_at: "2026-09-10T09:00:00Z",
    note: null,
    created_at: "2026-09-10T09:00:05Z",
    temp_min_c: 2,
    temp_max_c: 8,
    ...overrides,
  };
}

export function makeHandoff(container: Container, overrides: Partial<Handoff> = {}): Handoff {
  const fridge = makeLocation({ code: "FRIDGE", name: "冷藏冰箱", is_cold_storage: true });
  const bench = makeLocation({ code: "BENCH", name: "处理台", is_cold_storage: false });
  return {
    id: uid("handoff"),
    batch_id: uid("batch"),
    accession_number: "BATCH-001",
    container_id: container.id,
    container_label: container.label,
    from_location: fridge,
    to_location: bench,
    created_by: "alice",
    received_by: null,
    cancelled_by: null,
    rejected_by: null,
    rejected_at: null,
    reject_reason: null,
    reject_note: null,
    status: "pending",
    persisted_status: "pending",
    created_at: "2026-09-10T08:00:00Z",
    expires_at: "2026-09-10T08:10:00Z",
    received_at: null,
    cancelled_at: null,
    anomaly_at: null,
    anomaly_reason: null,
    resolved_at: null,
    resolution: null,
    successor_id: null,
    remaining_seconds: 600,
    server_time: "2026-09-10T08:00:00Z",
    exposure_seconds: 0,
    exposure_limit_seconds: 1800,
    exposure_exceeded: false,
    receipt_code: null,
    replayed: false,
    ...overrides,
  };
}

let checkSequence = 0;

export function makeInventoryItem(
  overrides: Partial<InventoryCheckItem> = {},
): InventoryCheckItem {
  checkSequence += 1;
  return {
    id: `check-item-${checkSequence}`,
    category: "matched",
    scanned_label: "TUBE-A",
    line_number: 1,
    container_id: "container-1",
    batch_id: "batch-1",
    accession_number: "BATCH-001",
    container_label: "TUBE-A",
    recorded_location_id: "loc-fridge",
    recorded_location_code: "FRIDGE",
    recorded_location_name: "冷藏冰箱",
    ...overrides,
  };
}

export function makeInventoryResult(
  overrides: Partial<InventoryCheckResult> = {},
): InventoryCheckResult {
  return {
    id: "check-1",
    location_id: "loc-fridge",
    checked_by: "night-a",
    created_at: "2026-09-10T20:00:00Z",
    matched_count: 1,
    missing_count: 0,
    misplaced_count: 0,
    unknown_count: 0,
    scanned_count: 1,
    location: makeLocation(),
    items: [makeInventoryItem()],
    recent_checks: [],
    ...overrides,
  };
}
