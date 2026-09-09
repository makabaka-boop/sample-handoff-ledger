import type { Batch, Handoff, Location, RejectReason } from "./types";

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
    HANDOFF_EXPIRED: "交接已超时，须标记异常并重开。",
    HANDOFF_CANCELLED: "交接已由发起人撤销。",
    HANDOFF_ANOMALY: "交接存在未解决异常，不能接收。",
    HANDOFF_REJECTED: "该交接已被夜班拒收，须重开后才能继续。",
    HANDOFF_ALREADY_RECEIVED: "该交接已确认接收，拒收无法再生效。",
    LOCATION_MISMATCH: "容器当前位置与交接来源不一致，请刷新核查。",
    EXPOSURE_LIMIT_EXCEEDED: "样本离柜时长已达上限，请隔离或复核。",
    UNRESOLVED_ANOMALY: "该批次仍有未解决异常，暂不能继续流转。",
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
    temperature_zone: string;
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
};
