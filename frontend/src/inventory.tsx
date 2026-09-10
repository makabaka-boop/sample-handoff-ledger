import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, errorMessage } from "./api";
import type {
  InventoryCheckCategory,
  InventoryCheckResult,
  InventoryCheckSummary,
  Location,
} from "./types";

// The scanner accumulates one label per line; surrounding whitespace is
// stripped per line. Blank lines are NOT filtered away here — blankLabelLines
// validates the raw scan so a gap is rejected instead of silently submitted.
export function parseScannedLabels(text: string): string[] {
  return text
    .split("\n")
    .map((label) => label.trim())
    .filter((label) => label.length > 0);
}

// Returns the 1-based positions of blank lines in the raw scan. The scanner's
// final carriage return only produces trailing blank lines, which are ignored;
// a blank line between or before labels means a missing/unreadable scan and
// must be rejected before submission.
export function blankLabelLines(text: string): number[] {
  const rawLines = text.split("\n");
  // The scanner's final carriage return only leaves trailing blank lines, so
  // ignore those; an internal or leading blank line is an unreadable/missing
  // scan and must be rejected.
  let end = rawLines.length;
  while (end > 0 && rawLines[end - 1].trim().length === 0) end -= 1;
  const lines = rawLines.slice(0, end);
  if (!lines.length) return [];
  return lines
    .map((line, index) => ({ line: line.trim(), number: index + 1 }))
    .filter((entry) => entry.line.length === 0)
    .map((entry) => entry.number);
}

export function duplicateLabels(labels: string[]): string[] {
  const seen = new Set<string>();
  const duplicated = new Set<string>();
  for (const label of labels) {
    // Align with the server's case-insensitive label set comparison.
    const folded = label.toLowerCase();
    if (seen.has(folded)) duplicated.add(label);
    seen.add(folded);
  }
  return [...duplicated];
}

const categoryOrder: InventoryCheckCategory[] = [
  "matched",
  "missing",
  "misplaced",
  "unknown",
];

const categoryHeadings: Record<InventoryCheckCategory, string> = {
  matched: "已匹配",
  missing: "账面缺失",
  misplaced: "错放到此处",
  unknown: "未知标签",
};

const categoryDescriptions: Record<InventoryCheckCategory, string> = {
  matched: "扫描标签与系统记录的本柜容器一致。",
  missing: "系统记录应在此处，但本次没有扫到；请先按交接记录核查。",
  misplaced: "系统记录在其他位置，需按原交接流程纠正，盘点不会自动移动。",
  unknown: "系统中没有对应的可流转容器标签。",
};

export function groupItems(result: InventoryCheckResult) {
  return categoryOrder
    .map((category) => ({
      category,
      items: result.items.filter((item) => item.category === category),
    }))
    .filter((group) => group.items.length > 0);
}

export function formatCheckTime(iso: string): string {
  return new Date(iso).toLocaleString("zh-CN");
}

function Counts({ result }: { result: InventoryCheckSummary }) {
  const cells: [InventoryCheckCategory, number][] = [
    ["matched", result.matched_count],
    ["missing", result.missing_count],
    ["misplaced", result.misplaced_count],
    ["unknown", result.unknown_count],
  ];
  return (
    <div className="inventory-counts" role="group" aria-label="分类计数">
      {cells.map(([category, count]) => (
        <span key={category} className={`inventory-count ${category}`}>
          {categoryHeadings[category]} <strong data-testid={`count-${category}`}>{count}</strong>
        </span>
      ))}
    </div>
  );
}

function ResultGroups({ result }: { result: InventoryCheckResult }) {
  const groups = useMemo(() => groupItems(result), [result]);
  return (
    <div className="inventory-groups">
      {groups.map(({ category, items }) => (
        <section key={category} className={`inventory-group ${category}`}>
          <h4>
            {categoryHeadings[category]}
            <span>{items.length}</span>
          </h4>
          <p className="group-hint">{categoryDescriptions[category]}</p>
          <ul>
            {items.map((item) => (
              <li key={item.id}>
                <strong>{item.scanned_label ?? item.container_label}</strong>
                {item.accession_number && <small>批次 {item.accession_number}</small>}
                {category === "misplaced" && (
                  <small className="misplaced-location">
                    系统记录位置：{item.recorded_location_name}（{item.recorded_location_code}）
                  </small>
                )}
                {category === "missing" && <small className="missing-hint">账面在柜、扫描未见</small>}
                {category === "unknown" && <small>无可流转容器记录</small>}
                {category === "matched" && <small>{item.recorded_location_name}</small>}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function RecentChecks({ location, recent }: { location: Location; recent: InventoryCheckSummary[] }) {
  if (!recent.length) return null;
  return (
    <div className="inventory-recent">
      <h3 className="timeline-title">{location.name} · 最近盘点记录</h3>
      <ol>
        {recent.map((check) => (
          <li key={check.id}>
            <span>{formatCheckTime(check.created_at)}</span>
            <small>
              盘点人 {check.checked_by} · 已匹配 {check.matched_count} · 账面缺失{" "}
              {check.missing_count} · 错放 {check.misplaced_count} · 未知{" "}
              {check.unknown_count}
            </small>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function InventoryPanel({ locations }: { locations: Location[] }) {
  const coldLocations = useMemo(
    () => locations.filter((location) => location.is_cold_storage),
    [locations],
  );
  const [locationId, setLocationId] = useState("");
  const [checkedBy, setCheckedBy] = useState("");
  const [labelText, setLabelText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<InventoryCheckResult | null>(null);
  const [history, setHistory] = useState<InventoryCheckSummary[]>([]);

  // Default to the first cold location once the location list arrives.
  useEffect(() => {
    if (!locationId && coldLocations.length) setLocationId(coldLocations[0].id);
  }, [coldLocations, locationId]);

  // Load the location's recent checks whenever the selection changes; a failed
  // load must never block scanning (the submission itself will surface errors).
  useEffect(() => {
    let cancelled = false;
    setResult(null);
    if (!locationId) {
      setHistory([]);
      return;
    }
    void api
      .inventoryChecks(locationId)
      .then((checks) => {
        if (!cancelled) setHistory(checks);
      })
      .catch(() => {
        if (!cancelled) setHistory([]);
      });
    return () => {
      cancelled = true;
    };
  }, [locationId]);

  const selectedLocation = coldLocations.find((item) => item.id === locationId) ?? null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const blankLines = blankLabelLines(labelText);
    const labels = parseScannedLabels(labelText);
    if (!labels.length) {
      setError("请先扫描至少一个容器标签。");
      return;
    }
    // A blank line in the middle (or at the start) is an unreadable/missing
    // scan: reject before any request so the server never receives a silently
    // shortened label list and creates a record.
    if (blankLines.length) {
      setError(
        `标签清单无效：第 ${blankLines.join("、")} 行为空白标签，请补扫或删除该行后再提交。`,
      );
      return;
    }
    const duplicated = duplicateLabels(labels);
    if (duplicated.length) {
      setError(`标签清单存在重复：${duplicated.join("、")}，每个标签在一次盘点中只能出现一次。`);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const next = await api.submitInventoryCheck(locationId, {
        checked_by: checkedBy,
        labels,
      });
      setResult(next);
      // Success clears the scanned labels only; the operator identity stays so
      // a second recount needs no retyping.
      setLabelText("");
      setHistory(next.recent_checks);
    } catch (caught) {
      // Offline or rejection must preserve every scanned label and the checker.
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="inventory-layout">
      <div className="inventory-copy">
        <span className="eyebrow">COLD STORAGE RECONCILIATION</span>
        <h1>位置盘点</h1>
        <p>
          逐行扫描冷藏位置内的容器标签并提交。服务端在同一事务中锁定该位置的可流转容器快照，
          按标签集合比对得出已匹配、账面缺失、错放到此处和未知标签。
        </p>
        <div className="trust-note"><span>✓</span> 盘点只形成核对证据，不会移动容器或改变批次处置</div>
        <div className="trust-note"><span>✓</span> 错放项附带系统记录位置，请按原交接流程纠正</div>
        <div className="trust-note"><span>✓</span> 断网或提交失败时，已扫标签与盘点人保留在表单中</div>
        {result && selectedLocation && <RecentChecks location={selectedLocation} recent={[result, ...history]} />}
        {!result && selectedLocation && <RecentChecks location={selectedLocation} recent={history} />}
      </div>
      <div className="inventory-work">
        <form className="inventory-form" onSubmit={submit}>
          <h2>选择冷藏位置并扫描</h2>
          <label htmlFor="inventory-location">冷藏位置</label>
          {coldLocations.length ? (
            <select
              id="inventory-location"
              value={locationId}
              onChange={(event) => setLocationId(event.target.value)}
              required
            >
              {coldLocations.map((location) => (
                <option value={location.id} key={location.id}>
                  {location.name}（{location.code}）
                </option>
              ))}
            </select>
          ) : (
            <p className="empty-log">暂无可盘点的冷藏位置。</p>
          )}
          <label htmlFor="inventory-checker">盘点人</label>
          <input
            id="inventory-checker"
            value={checkedBy}
            onChange={(event) => setCheckedBy(event.target.value)}
            maxLength={100}
            required
          />
          <label htmlFor="inventory-labels">容器标签（每行一个，按扫描顺序）</label>
          <textarea
            id="inventory-labels"
            value={labelText}
            onChange={(event) => setLabelText(event.target.value)}
            rows={8}
            placeholder={"TUBE-A\nTUBE-B"}
            spellCheck={false}
            required
          />
          {error && <div role="alert" className="notice error">{error}</div>}
          <button className="primary" disabled={busy || !coldLocations.length}>
            {busy ? "正在锁定快照并核对…" : "提交盘点"}
          </button>
        </form>
        {result && (
          <div className="inventory-result" aria-live="polite">
            <div className="inventory-result-head">
              <div>
                <span className="eyebrow">RECOUNT RESULT</span>
                <h2>本次盘点结果</h2>
                <p>
                  {result.location.name} · {result.checked_by} · {formatCheckTime(result.created_at)}
                </p>
              </div>
            </div>
            <Counts result={result} />
            <ResultGroups result={result} />
          </div>
        )}
      </div>
    </section>
  );
}
