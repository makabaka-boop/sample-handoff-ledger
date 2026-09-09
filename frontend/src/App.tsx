import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, errorMessage } from "./api";
import { createServerCountdown, formatDuration } from "./time";
import type { Batch, Handoff, Location, RejectReason } from "./types";

type View = "tasks" | "receive" | "batches";
type ReceiveMode = "confirm" | "reject";

const rejectReasons: { value: RejectReason; label: string }[] = [
  { value: "seal_broken", label: "容器封签破损" },
  { value: "label_mismatch", label: "标签不符" },
  { value: "package_contaminated", label: "包装污染" },
  { value: "other", label: "其他异常" },
];

export const rejectReasonLabel: Record<RejectReason, string> = {
  seal_broken: "容器封签破损",
  label_mismatch: "标签不符",
  package_contaminated: "包装污染",
  other: "其他异常",
};

const statusLabel = {
  pending: "等待接收",
  received: "已接收",
  cancelled: "已撤销",
  anomaly: "异常",
};

function Countdown({ handoff }: { handoff: Handoff }) {
  const countdown = useMemo(
    () => createServerCountdown(handoff.server_time, handoff.expires_at),
    [handoff.server_time, handoff.expires_at],
  );
  const [remaining, setRemaining] = useState(countdown.remainingSeconds());
  useEffect(() => {
    setRemaining(countdown.remainingSeconds());
    const timer = window.setInterval(() => setRemaining(countdown.remainingSeconds()), 1000);
    return () => window.clearInterval(timer);
  }, [countdown]);
  if (handoff.status !== "pending") return <span>{statusLabel[handoff.status]}</span>;
  return <span aria-label="剩余时间">剩余 {formatDuration(remaining)}</span>;
}

function Metric({ label, value, warning = false }: { label: string; value: string; warning?: boolean }) {
  return (
    <div className={`metric ${warning ? "warning" : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function HandoffCard({ item, onOpen }: { item: Handoff; onOpen: () => void }) {
  return (
    <button className={`handoff-card ${item.status}`} onClick={onOpen} type="button">
      <div className="card-topline">
        <span className={`status-pill ${item.status}`}>{statusLabel[item.status]}</span>
        <Countdown handoff={item} />
      </div>
      <h3>{item.accession_number}</h3>
      <p className="container-name">{item.container_label}</p>
      <div className="route">
        <span>{item.from_location.name}</span><b>→</b><span>{item.to_location.name}</span>
      </div>
      <div className="card-foot">
        <span>发起：{item.created_by}</span>
        <span>离柜 {formatDuration(item.exposure_seconds)}</span>
      </div>
    </button>
  );
}

export function ReceivePanel({ onDone }: { onDone: () => void }) {
  const [code, setCode] = useState("");
  const [receiver, setReceiver] = useState("");
  const [mode, setMode] = useState<ReceiveMode>("confirm");
  const [reason, setReason] = useState<RejectReason>("seal_broken");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setNotice(null);
    try {
      if (mode === "confirm") {
        const result = await api.confirm(code, receiver);
        setNotice({
          kind: "ok",
          text: result.replayed
            ? `已确认：${result.container_label} 早前已接收，当前位置未重复变更。`
            : `接收成功：${result.container_label} 已到达${result.to_location.name}。`,
        });
      } else {
        const result = await api.reject(code, receiver, reason, note);
        setNotice({
          kind: "ok",
          text: result.replayed
            ? `已记录：${result.container_label} 的早前拒收仍然有效，未重复写入。`
            : `拒收已记录：${result.container_label} 留在${result.from_location.name}，批次进入复核。`,
        });
      }
      setCode("");
      setReceiver("");
      setNote("");
      onDone();
    } catch (error) {
      // Failure or offline must preserve every field so the shift can retry as-is.
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  const rejecting = mode === "reject";

  return (
    <section className="receive-layout">
      <div className="receive-copy">
        <span className="eyebrow">ONE-TIME RECEIPT</span>
        <h2>输入六位交接码</h2>
        <p>位置只有在服务器确认事务完成后才会更新。断网或冲突不会清空你的输入。</p>
        <div className="trust-note"><span>✓</span> 截止时间由服务器判定，本机时钟不会改变结果</div>
        <div className="trust-note"><span>✓</span> 拒收与确认在同一事务中裁决，容器不会被移动</div>
      </div>
      <form className="receive-form" onSubmit={submit}>
        <div className="mode-toggle" role="radiogroup" aria-label="接收动作">
          <label className={mode === "confirm" ? "selected" : ""}>
            <input
              type="radio"
              name="receive-mode"
              value="confirm"
              checked={mode === "confirm"}
              onChange={() => setMode("confirm")}
            />
            确认接收
          </label>
          <label className={mode === "reject" ? "selected reject" : "reject"}>
            <input
              type="radio"
              name="receive-mode"
              value="reject"
              checked={mode === "reject"}
              onChange={() => setMode("reject")}
            />
            拒绝接收
          </label>
        </div>
        <label htmlFor="receipt-code">接收码</label>
        <input
          id="receipt-code"
          className="code-input"
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9]{6}"
          maxLength={6}
          placeholder="000000"
          value={code}
          onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
          required
        />
        <label htmlFor="receiver">{rejecting ? "拒收人" : "接收人"}</label>
        <input id="receiver" value={receiver} onChange={(e) => setReceiver(e.target.value)} required />
        {rejecting && <>
          <label htmlFor="reject-reason">拒收原因</label>
          <select
            id="reject-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value as RejectReason)}
            required
          >
            {rejectReasons.map((item) => (
              <option value={item.value} key={item.value}>{item.label}</option>
            ))}
          </select>
          <label htmlFor="reject-note">备注</label>
          <textarea
            id="reject-note"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={2000}
            rows={3}
            placeholder="描述封签、标签或包装的现场情况（可选）"
          />
        </>}
        {notice && <div role="alert" className={`notice ${notice.kind}`}>{notice.text}</div>}
        <button className={rejecting ? "danger" : "primary"} disabled={busy || code.length !== 6}>
          {busy ? "正在由服务器裁决…" : rejecting ? "拒绝接收并形成异常" : "确认接收"}
        </button>
      </form>
    </section>
  );
}

function NewBatchForm({ locations, onCreated }: { locations: Location[]; onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [notice, setNotice] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setNotice("");
    try {
      await api.createBatch({
        accession_number: String(data.get("accession")),
        temperature_zone: String(data.get("zone")),
        max_out_minutes: Number(data.get("limit")),
        created_by: String(data.get("actor")),
        containers: [{
          label: String(data.get("container")),
          initial_location_code: String(data.get("location")),
        }],
      });
      setOpen(false);
      onCreated();
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }
  if (!open) return <button className="secondary" onClick={() => setOpen(true)}>＋ 登记批次</button>;
  return (
    <form className="inline-form" onSubmit={submit}>
      <div className="form-title"><strong>登记新批次</strong><button type="button" onClick={() => setOpen(false)}>×</button></div>
      <label>批次号<input name="accession" required /></label>
      <label>温区<input name="zone" placeholder="2–8°C" required /></label>
      <label>最长离柜（分钟）<input name="limit" type="number" min="1" defaultValue="30" required /></label>
      <label>容器标签<input name="container" required /></label>
      <label>初始位置<select name="location">{locations.map((l) => <option value={l.code} key={l.id}>{l.name}</option>)}</select></label>
      <label>登记人<input name="actor" required /></label>
      {notice && <div className="notice error">{notice}</div>}
      <button className="primary">保存批次</button>
    </form>
  );
}

function NewHandoffForm({ batches, locations, onCreated }: {
  batches: Batch[];
  locations: Location[];
  onCreated: (handoff: Handoff) => void;
}) {
  const [open, setOpen] = useState(false);
  const [containerId, setContainerId] = useState("");
  const [notice, setNotice] = useState("");
  const containers = batches.flatMap((batch) => batch.containers.map((container) => ({ batch, container })));
  const chosen = containers.find(({ container }) => container.id === containerId);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    if (!chosen) return;
    setNotice("");
    try {
      const result = await api.createHandoff({
        container_id: chosen.container.id,
        from_location_code: chosen.container.current_location.code,
        to_location_code: String(data.get("destination")),
        created_by: String(data.get("actor")),
        ttl_minutes: Number(data.get("ttl")),
      });
      setOpen(false);
      onCreated(result);
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }
  if (!open) return <button className="primary compact" onClick={() => setOpen(true)}>发起交接</button>;
  return (
    <form className="inline-form handoff-form" onSubmit={submit}>
      <div className="form-title"><strong>发起交接</strong><button type="button" onClick={() => setOpen(false)}>×</button></div>
      <label>容器<select value={containerId} onChange={(e) => setContainerId(e.target.value)} required><option value="">请选择</option>{containers.map(({ batch, container }) => <option value={container.id} key={container.id}>{batch.accession_number} · {container.label}（{container.current_location.name}）</option>)}</select></label>
      <label>目的位置<select name="destination" required><option value="">请选择</option>{locations.filter((l) => l.id !== chosen?.container.current_location.id).map((l) => <option value={l.code} key={l.id}>{l.name}</option>)}</select></label>
      <label>有效期（分钟）<input name="ttl" type="number" min="1" defaultValue="10" required /></label>
      <label>发起人<input name="actor" required /></label>
      {notice && <div className="notice error">{notice}</div>}
      <button className="primary">生成一次性接收码</button>
    </form>
  );
}

function BatchPanel({ batches, locations, refresh, openBatch }: {
  batches: Batch[];
  locations: Location[];
  refresh: () => void;
  openBatch: (id: string) => void;
}) {
  return (
    <section>
      <div className="section-heading">
        <div><span className="eyebrow">SPECIMEN REGISTER</span><h2>批次与容器</h2></div>
        <NewBatchForm locations={locations} onCreated={refresh} />
      </div>
      <div className="batch-grid">
        {batches.map((batch) => (
          <button className="batch-card" key={batch.id} onClick={() => openBatch(batch.id)}>
            <div className="batch-card-head"><h3>{batch.accession_number}</h3>{batch.has_unresolved_anomaly && <span className="status-pill anomaly">待处理异常</span>}</div>
            <p>{batch.temperature_zone} · 离柜上限 {batch.max_out_minutes} 分钟</p>
            {batch.containers.map((container) => (
              <div className="container-row" key={container.id}>
                <span><b>{container.label}</b><small>{container.current_location.name}</small></span>
                <span className={container.exposure_exceeded ? "red" : ""}>{formatDuration(container.total_out_seconds)} / {formatDuration(container.max_out_seconds)}</span>
              </div>
            ))}
          </button>
        ))}
        {!batches.length && <div className="empty">尚无批次。先登记一个批次和容器。</div>}
      </div>
    </section>
  );
}

function DetailDrawer({ handoff, batch, close, refresh, showCode }: {
  handoff: Handoff | null;
  batch: Batch | null;
  close: () => void;
  refresh: () => void;
  showCode: string | null;
}) {
  const [actor, setActor] = useState("");
  const [notice, setNotice] = useState("");
  const [decision, setDecision] = useState("review");
  const [decisionNote, setDecisionNote] = useState("");
  if (!handoff && !batch) return null;

  async function action(kind: "cancel" | "reopen" | "resolve") {
    if (!handoff || !actor) return;
    setNotice("");
    try {
      const result = kind === "cancel"
        ? await api.cancel(handoff.id, actor)
        : kind === "reopen"
          ? await api.reopen(handoff.id, actor)
          : await api.resolve(handoff.id, actor, decision, decisionNote);
      if (result.receipt_code) setNotice(`新接收码：${result.receipt_code}（仅显示一次）`);
      else setNotice("操作已记录到责任链。");
      refresh();
    } catch (error) { setNotice(errorMessage(error)); }
  }

  return (
    <div className="drawer-backdrop" onMouseDown={close}>
      <aside className="drawer" onMouseDown={(e) => e.stopPropagation()} aria-label="详情">
        <button className="drawer-close" onClick={close}>×</button>
        {handoff && <>
          <span className={`status-pill ${handoff.status}`}>{statusLabel[handoff.status]}</span>
          <h2>{handoff.accession_number} · {handoff.container_label}</h2>
          <p className="large-route">{handoff.from_location.name} <b>→</b> {handoff.to_location.name}</p>
          {(showCode || handoff.receipt_code) && <div className="receipt-code"><span>一次性接收码</span><strong>{showCode || handoff.receipt_code}</strong><small>关闭后不再显示</small></div>}
          <div className="metric-grid">
            <Metric label="交接时限" value={handoff.status === "pending" ? `${handoff.remaining_seconds} 秒` : statusLabel[handoff.status]} warning={handoff.status === "anomaly"} />
            <Metric label="累计离柜" value={formatDuration(handoff.exposure_seconds)} warning={handoff.exposure_exceeded} />
          </div>
          <dl>
            <dt>发起人</dt><dd>{handoff.created_by}</dd>
            <dt>接收人</dt><dd>{handoff.received_by ?? "—"}</dd>
            <dt>拒收人</dt>
            <dd>
              {handoff.rejected_by
                ? `${handoff.rejected_by}${handoff.rejected_at ? ` · ${new Date(handoff.rejected_at).toLocaleString("zh-CN")}` : ""}`
                : "—"}
            </dd>
            <dt>拒收原因</dt>
            <dd>{handoff.reject_reason ? rejectReasonLabel[handoff.reject_reason] : "—"}</dd>
            <dt>拒收备注</dt><dd>{handoff.reject_note ?? "—"}</dd>
            <dt>异常原因</dt><dd>{handoff.anomaly_reason ?? "—"}</dd>
          </dl>
          {(handoff.status === "pending" || handoff.status === "anomaly") && <div className="actions"><input placeholder="操作人" value={actor} onChange={(e) => setActor(e.target.value)} />{handoff.status === "pending" ? <button onClick={() => action("cancel")}>撤销交接</button> : <><button className="primary" onClick={() => action("reopen")}>重开交接</button><select aria-label="异常处置" value={decision} onChange={(e) => setDecision(e.target.value)}><option value="isolate">隔离</option><option value="review">复核</option><option value="release">放行</option></select><input placeholder="处置说明" value={decisionNote} onChange={(e) => setDecisionNote(e.target.value)} /><button disabled={!decisionNote} onClick={() => action("resolve")}>记录处置</button></>}</div>}
        </>}
        {batch && <>
          <span className="eyebrow">CHAIN OF CUSTODY</span><h2>{batch.accession_number}</h2>
          <div className="metric-grid"><Metric label="温区" value={batch.temperature_zone} /><Metric label="批次状态" value={batch.disposition} warning={batch.has_unresolved_anomaly} /></div>
          <h3 className="timeline-title">完整责任链</h3>
          <ol className="timeline">{batch.timeline?.map((event) => <li key={event.id}><span></span><div><b>{event.event_type.replaceAll("_", " ")}</b><p>{event.actor} · {new Date(event.occurred_at).toLocaleString("zh-CN")}</p>{event.note && <small>{event.note}</small>}</div></li>)}</ol>
        </>}
        {notice && <div role="alert" className="notice ok">{notice}</div>}
      </aside>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState<View>("tasks");
  const [handoffs, setHandoffs] = useState<Handoff[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [selectedHandoff, setSelectedHandoff] = useState<Handoff | null>(null);
  const [selectedBatch, setSelectedBatch] = useState<Batch | null>(null);
  const [freshCode, setFreshCode] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    try {
      const [nextHandoffs, nextBatches, nextLocations] = await Promise.all([
        api.handoffs(), api.batches(), api.locations(),
      ]);
      setHandoffs(nextHandoffs);
      setBatches(nextBatches);
      setLocations(nextLocations);
      setOffline(false);
      setSelectedHandoff((current) =>
        current ? (nextHandoffs.find((item) => item.id === current.id) ?? current) : null,
      );
    } catch { setOffline(true); }
    finally { setLoading(false); }
  }

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, []);

  async function openBatch(id: string) {
    try { setSelectedBatch(await api.batch(id)); } catch { setOffline(true); }
  }

  async function openHandoff(item: Handoff, code: string | null = null) {
    setSelectedHandoff(item);
    setFreshCode(code);
    try { setSelectedBatch(await api.batch(item.batch_id)); } catch { setOffline(true); }
  }

  const active = handoffs.filter((item) => item.status === "pending" || (item.status === "anomaly" && !item.resolved_at));
  const anomalyCount = active.filter((item) => item.status === "anomaly").length;

  return (
    <div className="app-shell">
      <header>
        <div className="brand"><span className="brand-mark">S</span><div><strong>样本交接台</strong><small>NIGHT SHIFT LEDGER</small></div></div>
        <nav>{(["tasks", "receive", "batches"] as View[]).map((item) => <button className={view === item ? "active" : ""} onClick={() => setView(item)} key={item}>{item === "tasks" ? "交接待办" : item === "receive" ? "短码接收" : "批次档案"}</button>)}</nav>
        <div className="connection"><i className={offline ? "off" : ""}></i>{offline ? "连接中断" : "系统在线"}</div>
      </header>
      {offline && <div className="offline-banner">无法连接服务器。页面不会预先改变任何状态；输入会保留，连接恢复后可重试。<button onClick={() => void refresh()}>立即重试</button></div>}
      <main>
        {view === "tasks" && <section>
          <div className="section-heading"><div><span className="eyebrow">SHIFT OVERVIEW</span><h1>今晚的交接</h1><p>{active.length} 项待处理 · {anomalyCount} 项需要立即核查</p></div><NewHandoffForm batches={batches} locations={locations} onCreated={(result) => { void openHandoff(result, result.receipt_code); void refresh(); }} /></div>
          {loading ? <div className="empty">正在读取真实交接记录…</div> : <div className="task-grid">{active.map((item) => <HandoffCard item={item} key={item.id} onOpen={() => void openHandoff(item)} />)}{!active.length && <div className="empty">当前没有待办交接。</div>}</div>}
          {!!handoffs.filter((item) => item.status === "received").length && <><h2 className="subheading">最近完成</h2><div className="task-grid compact-grid">{handoffs.filter((item) => item.status === "received").slice(0, 4).map((item) => <HandoffCard item={item} key={item.id} onOpen={() => void openHandoff(item)} />)}</div></>}
        </section>}
        {view === "receive" && <ReceivePanel onDone={() => void refresh()} />}
        {view === "batches" && <BatchPanel batches={batches} locations={locations} refresh={() => void refresh()} openBatch={(id) => void openBatch(id)} />}
      </main>
      <DetailDrawer handoff={selectedHandoff} batch={selectedBatch} close={() => { setSelectedHandoff(null); setSelectedBatch(null); setFreshCode(null); }} refresh={() => void refresh()} showCode={freshCode} />
    </div>
  );
}
