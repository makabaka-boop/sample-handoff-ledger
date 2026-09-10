export type Status = "pending" | "received" | "cancelled" | "anomaly";

export interface Location {
  id: string;
  code: string;
  name: string;
  is_cold_storage: boolean;
}

export type InventoryCheckCategory = "matched" | "missing" | "misplaced" | "unknown";

export interface InventoryCheckItem {
  id: string;
  category: InventoryCheckCategory;
  scanned_label: string | null;
  line_number: number;
  container_id: string | null;
  batch_id: string | null;
  accession_number: string | null;
  container_label: string | null;
  recorded_location_id: string | null;
  recorded_location_code: string | null;
  recorded_location_name: string | null;
}

export interface InventoryCheckSummary {
  id: string;
  location_id: string;
  checked_by: string;
  created_at: string;
  matched_count: number;
  missing_count: number;
  misplaced_count: number;
  unknown_count: number;
  scanned_count: number;
  location: Location;
}

export interface InventoryCheckResult extends InventoryCheckSummary {
  items: InventoryCheckItem[];
  recent_checks: InventoryCheckSummary[];
}
export type ContainerStatus = "active" | "replaced";

export interface Container {
  id: string;
  label: string;
  current_location: Location;
  accumulated_out_seconds: number;
  current_out_seconds: number;
  total_out_seconds: number;
  max_out_seconds: number;
  exposure_exceeded: boolean;
  out_since: string | null;
  updated_at: string;
  status: ContainerStatus;
  replacement_container_id: string | null;
  replaced_by: string | null;
  replaced_at: string | null;
  replacement_reason: string | null;
}

export interface Batch {
  id: string;
  accession_number: string;
  temperature_zone: string;
  temp_min_c: number;
  temp_max_c: number;
  max_out_minutes: number;
  disposition: "active" | "isolated" | "review" | "released";
  created_at: string;
  has_unresolved_anomaly: boolean;
  containers: Container[];
  timeline?: TimelineEvent[];
  temperature_observations?: TemperatureObservation[];
}

export type TemperatureVerdict = "normal" | "out_of_range";

export interface TemperatureObservation {
  id: string;
  batch_id: string;
  container_id: string;
  temperature_c: number;
  verdict: TemperatureVerdict;
  measured_by: string;
  observed_at: string;
  note: string | null;
  created_at: string;
  temp_min_c: number;
  temp_max_c: number;
}

export interface TimelineEvent {
  id: string;
  container_id: string | null;
  handoff_id: string | null;
  event_type: string;
  actor: string;
  occurred_at: string;
  details: Record<string, unknown>;
  note: string | null;
}

export type RejectReason =
  | "seal_broken"
  | "label_mismatch"
  | "package_contaminated"
  | "other";

export interface Handoff {
  id: string;
  batch_id: string;
  accession_number: string;
  container_id: string;
  container_label: string;
  from_location: Location;
  to_location: Location;
  created_by: string;
  received_by: string | null;
  cancelled_by: string | null;
  rejected_by: string | null;
  rejected_at: string | null;
  reject_reason: RejectReason | null;
  reject_note: string | null;
  status: Status;
  persisted_status: Status;
  created_at: string;
  expires_at: string;
  received_at: string | null;
  cancelled_at: string | null;
  anomaly_at: string | null;
  anomaly_reason: string | null;
  resolved_at: string | null;
  resolution: string | null;
  successor_id: string | null;
  remaining_seconds: number;
  server_time: string;
  exposure_seconds: number;
  exposure_limit_seconds: number;
  exposure_exceeded: boolean;
  receipt_code: string | null;
  replayed: boolean;
}
