import { FormEvent, useState } from "react";
import { api, errorMessage } from "./api";
import type { Batch, Container, TemperatureObservation } from "./types";

// datetime-local inputs use the browser's local zone; normalise to an explicit
// UTC instant so the server never interprets a naive timestamp as its own zone.
export function localInputToUtcIso(value: string): string {
  return new Date(value).toISOString();
}

export function utcIsoToLocalInput(iso: string): string {
  const date = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

export function defaultObservedAt(): string {
  const date = new Date(Date.now() - 60_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

const verdictLabel = {
  normal: "正常",
  out_of_range: "越界",
} as const;

export function TemperatureObservationForm({
  container,
  batch,
  onRecorded,
}: {
  container: Container;
  batch: Batch;
  onRecorded: (detail: Batch) => void;
}) {
  const [open, setOpen] = useState(false);
  const [temperature, setTemperature] = useState("");
  const [measuredBy, setMeasuredBy] = useState("");
  const [observedAt, setObservedAt] = useState(defaultObservedAt());
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const detail = await api.recordTemperature(container.id, {
        temperature_c: Number(temperature),
        measured_by: measuredBy,
        observed_at: localInputToUtcIso(observedAt),
        note: note.trim() ? note : null,
      });
      setOpen(false);
      setTemperature("");
      setMeasuredBy("");
      setNote("");
      onRecorded(detail);
    } catch (err) {
      // The transaction rolled back; keep every field so the operator can fix
      // the value or time and resubmit without retyping.
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button className="secondary compact" type="button" onClick={() => setOpen(true)}>
        补录人工测温
      </button>
    );
  }

  return (
    <form className="inline-form temperature-form" onSubmit={submit}>
      <div className="form-title">
        <strong>补录测温 {container.label}</strong>
        <button
          type="button"
          aria-label="关闭测温表单"
          onClick={() => {
            setOpen(false);
            setError("");
          }}
        >
          ×
        </button>
      </div>
      <p className="range-hint">批次温区 {batch.temp_min_c}–{batch.temp_max_c}°C，边界值判为正常。</p>
      <label>摄氏温度 (°C)
        <input
          value={temperature}
          onChange={(event) => setTemperature(event.target.value)}
          type="number"
          step="0.1"
          inputMode="decimal"
          required
          autoFocus
        />
      </label>
      <label>测量人
        <input
          value={measuredBy}
          onChange={(event) => setMeasuredBy(event.target.value)}
          required
          maxLength={100}
        />
      </label>
      <label>测量时间
        <input
          value={observedAt}
          onChange={(event) => setObservedAt(event.target.value)}
          type="datetime-local"
          required
        />
      </label>
      <label>备注
        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          maxLength={2000}
          rows={2}
        />
      </label>
      {error && <div role="alert" className="notice error">{error}</div>}
      <button className="primary" disabled={busy}>
        {busy ? "服务器事务处理中…" : "提交测温判定"}
      </button>
    </form>
  );
}

export function TemperatureLog({ observations }: { observations: TemperatureObservation[] }) {
  if (!observations.length) {
    return (
      <div className="temperature-log">
        <h3 className="timeline-title">人工测温记录</h3>
        <p className="empty-log">尚无人工测温记录。在可流转容器上“补录人工测温”。</p>
      </div>
    );
  }
  return (
    <div className="temperature-log">
      <h3 className="timeline-title">人工测温记录</h3>
      <ol>
        {observations.map((observation) => (
          <li key={observation.id} className={observation.verdict === "normal" ? "ok" : "bad"}>
            <div className="temp-row">
              <strong>{observation.temperature_c}°C</strong>
              <span
                className={`status-pill ${observation.verdict === "normal" ? "received" : "anomaly"}`}
              >
                {verdictLabel[observation.verdict]}
              </span>
            </div>
            <p>
              {observation.measured_by} · 测量于 {new Date(observation.observed_at).toLocaleString("zh-CN")}
            </p>
            {observation.note && <small>{observation.note}</small>}
          </li>
        ))}
      </ol>
    </div>
  );
}
