import type {
  Batch,
  Container,
  Handoff,
  InventoryCheckResult,
  InventoryCheckSummary,
  Location,
  RejectReason,
} from "./types";

export interface TemperatureObservationInput {
  temperature_c: number;
  measured_by: string;
  observed_at: string;
  note: string | null;
}

const API_URL = import.meta.env.VITE_API_URL ?? "/api";

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public retryable: boolean,
    public traceId?: string,
  ) {
    super(message);
  }
}

export function errorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "网络连接中断，输入已保留，请恢复后重试。";
  const messages: Record<string, string> = {
    INVALID_RECEIPT_CODE: "接收码无效，请核对六位数字。",
    HANDOFF_NOT_FOUND: "交接记录不存在，请刷新后重试。",
    NOT_HANDOFF_CREATOR: "只有交接发起人可以执行该操作。",
    HANDOFF_EXPIRED: "交接已超时，须标记异常并重开。",
    HANDOFF_CANCELLED: "交接已由发起人撤销。",
    HANDOFF_ANOMALY: "交接存在未解决异常，不能接收。",
    HANDOFF_REJECTED: "该交接已被夜班拒收，须重开后才能继续。",
    HANDOFF_ALREADY_RECEIVED: "该交接已确认接收，无法再进行其他操作。",
    SAME_LOCATION: "新目标位置不能与来源位置相同。",
    REROUTE_TARGET_UNCHANGED: "新目标与当前目标相同，无需改派。",
    REROUTE_ENVIRONMENT_MISMATCH: "新旧目标的冷藏属性不一致，不能改派到该位置。",
    LOCATION_NOT_FOUND: "目标位置不存在，请刷新后重试。",
    LOCATION_MISMATCH: "容器当前位置与交接来源不一致，请刷新核查。",
    EXPOSURE_LIMIT_EXCEEDED: "样本离柜时长已达上限，请隔离或复核。",
    UNRESOLVED_ANOMALY: "该批次仍有未解决异常，暂不能继续流转。",
    CONTAINER_ALREADY_REPLACED: "该容器已封存，请使用转装后的新容器继续交接。",
    HANDOFF_ALREADY_PENDING: "该容器存在待接收交接，请先完成或撤销后再转装。",
    CONTAINER_LABEL_EXISTS: "批次内已存在相同的容器标签，请换一个新标签。",
    BATCH_NOT_ACTIVE: "批次当前不可流转，暂不能转装替换。",
    INVALID_OBSERVED_AT: "测量时间无效：不能晚于当前时间，也不能早于批次创建时间。",
    TEMPERATURE_OUT_OF_RANGE: "测温结果超出批次温区，批次已进入复核。",
    INVALID_INVENTORY_LABELS: "标签清单无效：不能包含空白或重复标签，请核对后重新扫描。",
    INVALID_INVENTORY_CHECKED_BY: "盘点人无效：请填写实际盘点人员姓名，不能只有空格。",
    LOCATION_NOT_COLD_STORAGE: "该位置不是冷藏位置，不能提交盘点。",
    DATABASE_UNAVAILABLE: "数据库暂时不可用，输入已保留，请稍后重试。",
    TRANSACTION_CONFLICT: "同时发生了另一项操作，请刷新后重试。",
  };
  const trace = error.traceId ? `（追踪号 ${error.traceId}）` : "";
  return `${messages[error.code] ?? error.message}${trace}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      signal: init?.signal ?? AbortSignal.timeout(8_000),
    });
  } catch {
    throw new Error("network unavailable");
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = payload?.error;
    throw new ApiError(
      detail?.code ?? "HTTP_ERROR",
      detail?.message ?? `请求失败 (${response.status})`,
      detail?.retryable ?? false,
      detail?.trace_id ?? response.headers.get("X-Trace-ID") ?? undefined,
    );
  }
  return response.json() as Promise<T>;
}

export const api = {
  locations: () => request<Location[]>("/locations"),
  batches: () => request<Batch[]>("/batches"),
  batch: (id: string) => request<Batch>(`/batches/${id}`),
  replaceContainer: (
    id: string,
    data: { new_label: string; actor: string; reason: string; note: string | null },
  ) =>
    request<Batch>(`/containers/${id}/replace`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  recordTemperature: (id: string, data: TemperatureObservationInput) =>
    request<Batch>(`/containers/${id}/temperature-observations`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  handoffs: () => request<Handoff[]>("/handoffs"),
  handoff: (id: string) => request<Handoff>(`/handoffs/${id}`),
  confirm: (code: string, receivedBy: string) =>
    request<Handoff>("/handoffs/confirm", {
      method: "POST",
      body: JSON.stringify({ code, received_by: receivedBy }),
    }),
  reject: (code: string, rejectedBy: string, reason: RejectReason, note: string) =>
    request<Handoff>("/handoffs/reject", {
      method: "POST",
      body: JSON.stringify({
        code,
        rejected_by: rejectedBy,
        reason,
        note: note.trim() ? note : null,
      }),
    }),
  createBatch: (data: {
    accession_number: string;
    temp_min_c: number;
    temp_max_c: number;
    max_out_minutes: number;
    created_by: string;
    containers: { label: string; initial_location_code: string }[];
  }) => request<Batch>("/batches", { method: "POST", body: JSON.stringify(data) }),
  createHandoff: (data: {
    container_id: string;
    from_location_code: string;
    to_location_code: string;
    created_by: string;
    ttl_minutes: number;
  }) => request<Handoff>("/handoffs", { method: "POST", body: JSON.stringify(data) }),
  cancel: (id: string, actor: string) =>
    request<Handoff>(`/handoffs/${id}/cancel`, {
      method: "POST",
      body: JSON.stringify({ actor }),
    }),
  reroute: (id: string, data: { actor: string; to_location_code: string; reason: string }) =>
    request<Handoff>(`/handoffs/${id}/reroute`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  reopen: (id: string, actor: string) =>
    request<Handoff>(`/handoffs/${id}/reopen`, {
      method: "POST",
      body: JSON.stringify({ actor }),
    }),
  resolve: (id: string, actor: string, decision: string, note: string) =>
    request<Handoff>(`/handoffs/${id}/resolve`, {
      method: "POST",
      body: JSON.stringify({ actor, decision, note }),
    }),
  submitInventoryCheck: (
    locationId: string,
    data: { checked_by: string; labels: string[] },
  ) =>
    request<InventoryCheckResult>(`/locations/${locationId}/inventory-checks`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  inventoryChecks: (locationId: string) =>
    request<InventoryCheckSummary[]>(`/locations/${locationId}/inventory-checks`),
  inventoryCheck: (checkId: string) =>
    request<InventoryCheckResult>(`/inventory-checks/${checkId}`),
};
