import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BatchContainers, NewHandoffForm, ReplaceContainerForm } from "./App";
import { api, ApiError } from "./api";
import { makeBatch, makeContainer, makeHandoff, makeLocation } from "./test/fixtures";
import type { Batch, Handoff } from "./types";

const noop = () => {};

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

function openReplaceForm() {
  fireEvent.click(screen.getByRole("button", { name: "转装替换" }));
}

function fillReplaceForm() {
  fireEvent.change(screen.getByLabelText("新容器标签"), { target: { value: "TUBE-B" } });
  fireEvent.change(screen.getByLabelText("操作人"), { target: { value: "carol" } });
  fireEvent.change(screen.getByLabelText("替换原因"), { target: { value: "外壳碎裂" } });
  fireEvent.change(screen.getByLabelText("备注"), { target: { value: "盘点发现外壁裂纹" } });
}

describe("container replacement form", () => {
  it("posts the replacement payload and reports the successor on success", async () => {
    const old = makeContainer();
    const successor = makeContainer({
      id: "container-new",
      label: "TUBE-B",
      accumulated_out_seconds: 90,
      out_since: "2026-09-10T07:58:30Z",
      total_out_seconds: 90,
    });
    const sealed = makeContainer({
      id: old.id,
      label: "TUBE-A",
      status: "replaced",
      replacement_container_id: "container-new",
      replaced_by: "carol",
      replaced_at: "2026-09-10T08:00:00Z",
      replacement_reason: "外壳碎裂",
      total_out_seconds: 90,
    });
    const detail: Batch = makeBatch({ containers: [sealed, successor] });
    const spy = vi.spyOn(api, "replaceContainer").mockResolvedValue(detail);
    const onReplaced = vi.fn();
    render(<ReplaceContainerForm container={old} onReplaced={onReplaced} />);

    openReplaceForm();
    fillReplaceForm();
    fireEvent.click(screen.getByRole("button", { name: "封存原容器并转装" }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith(old.id, {
      new_label: "TUBE-B",
      actor: "carol",
      reason: "外壳碎裂",
      note: "盘点发现外壁裂纹",
    });
    await waitFor(() => expect(onReplaced).toHaveBeenCalledWith(detail, "TUBE-B"));
    // Form collapsed back to the transload trigger after success.
    expect(screen.queryByRole("button", { name: "封存原容器并转装" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "转装替换" })).toBeInTheDocument();
  });

  it("keeps every input and shows the mapped error when the transaction rolls back", async () => {
    const old = makeContainer();
    vi.spyOn(api, "replaceContainer").mockRejectedValue(
      new ApiError("CONTAINER_LABEL_EXISTS", "dup", false, "trace-9"),
    );
    render(<ReplaceContainerForm container={old} onReplaced={noop} />);

    openReplaceForm();
    fillReplaceForm();
    fireEvent.click(screen.getByRole("button", { name: "封存原容器并转装" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("相同的容器标签"));
    expect(screen.getByRole("alert")).toHaveTextContent("trace-9");
    expect(screen.getByLabelText("新容器标签")).toHaveValue("TUBE-B");
    expect(screen.getByLabelText("操作人")).toHaveValue("carol");
    expect(screen.getByLabelText("替换原因")).toHaveValue("外壳碎裂");
    expect(screen.getByLabelText("备注")).toHaveValue("盘点发现外壁裂纹");
  });

  it("sends null for a blank note", async () => {
    const old = makeContainer();
    const detail = makeBatch({
      containers: [makeContainer({ id: old.id, status: "replaced" }), makeContainer({ label: "TUBE-B" })],
    });
    const spy = vi.spyOn(api, "replaceContainer").mockResolvedValue(detail);
    render(<ReplaceContainerForm container={old} onReplaced={noop} />);
    openReplaceForm();
    fireEvent.change(screen.getByLabelText("新容器标签"), { target: { value: "TUBE-B" } });
    fireEvent.change(screen.getByLabelText("操作人"), { target: { value: "carol" } });
    fireEvent.change(screen.getByLabelText("替换原因"), { target: { value: "裂" } });
    fireEvent.change(screen.getByLabelText("备注"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "封存原容器并转装" }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy.mock.calls[0][1].note).toBeNull();
  });
});

describe("batch container list", () => {
  it("offers transloading only for active containers and marks sealed ones read-only", () => {
    const active = makeContainer({ id: "c1", label: "TUBE-B" });
    const sealed = makeContainer({
      id: "c0",
      label: "TUBE-A",
      status: "replaced",
      replacement_container_id: "c1",
      replaced_by: "carol",
      replaced_at: "2026-09-10T08:00:00Z",
      replacement_reason: "外壳碎裂",
    });
    const batch = makeBatch({ containers: [active, sealed] });
    render(<BatchContainers batch={batch} onReplaced={noop} />);

    expect(screen.getAllByRole("button", { name: "转装替换" })).toHaveLength(1);
    expect(screen.getByText("已封存")).toBeInTheDocument();
    expect(screen.getByText("可流转")).toBeInTheDocument();
    expect(screen.getByText(/外壳碎裂/)).toBeInTheDocument();
  });

  it("shows exactly one replacement record in the chain after success", async () => {
    const old = makeContainer({ id: "c0", label: "TUBE-A" });
    const batch = makeBatch({
      containers: [old],
      timeline: [
        {
          id: "ev1",
          container_id: old.id,
          handoff_id: null,
          event_type: "container_registered",
          actor: "alice",
          occurred_at: "2026-09-10T08:00:00Z",
          details: { label: "TUBE-A" },
          note: null,
        },
      ],
    });
    const successor = makeContainer({ id: "c1", label: "TUBE-B", total_out_seconds: 120 });
    const refreshed = makeBatch({
      containers: [
        makeContainer({
          id: "c0",
          label: "TUBE-A",
          status: "replaced",
          replacement_container_id: "c1",
          replaced_by: "carol",
          replaced_at: "2026-09-10T08:05:00Z",
          replacement_reason: "外壳碎裂",
        }),
        successor,
      ],
      timeline: [
        {
          id: "ev2",
          container_id: "c0",
          handoff_id: null,
          event_type: "container_replaced",
          actor: "carol",
          occurred_at: "2026-09-10T08:05:00Z",
          details: { old_label: "TUBE-A", new_label: "TUBE-B", new_container_id: "c1" },
          note: null,
        },
        ...(batch.timeline ?? []),
      ],
    });
    const spy = vi.spyOn(api, "replaceContainer").mockResolvedValue(refreshed);
    function Harness() {
      const [shown, setShown] = useState<Batch>(batch);
      return <BatchContainers batch={shown} onReplaced={setShown} />;
    }
    render(<Harness />);

    openReplaceForm();
    fillReplaceForm();
    fireEvent.click(screen.getByRole("button", { name: "封存原容器并转装" }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText(/继承离柜计时/)).toBeVisible());
    // The sealed container shows no action; only the new active container can transload again.
    expect(screen.getAllByRole("button", { name: "转装替换" })).toHaveLength(1);
    expect(screen.getByText("TUBE-B")).toBeInTheDocument();
    expect(screen.getByText("已封存")).toBeInTheDocument();
  });
});

describe("new handoff form", () => {
  it("lists only active containers so sealed ones cannot start handoffs", () => {
    const active = makeContainer({
      id: "c1",
      label: "TUBE-B",
      current_location: makeLocation({ id: "l-bench", code: "BENCH", name: "处理台", is_cold_storage: false }),
    });
    const sealed = makeContainer({ id: "c0", label: "TUBE-A", status: "replaced" });
    const batch = makeBatch({ accession_number: "BATCH-X", containers: [active, sealed] });
    const locations = [
      makeLocation({ id: "l-bench", code: "BENCH", name: "处理台", is_cold_storage: false }),
      makeLocation({ id: "l-window", code: "WINDOW", name: "交接窗", is_cold_storage: false }),
    ];
    const onCreated = vi.fn();
    render(<NewHandoffForm batches={[batch]} locations={locations} onCreated={onCreated} />);
    fireEvent.click(screen.getByRole("button", { name: "发起交接" }));
    const options = screen.getAllByRole("option").map((option) => option.textContent);
    expect(options.some((text) => text?.includes("TUBE-A"))).toBe(false);
    expect(options.some((text) => text?.includes("TUBE-B"))).toBe(true);
  });

  it("starts a handoff against the active replacement container", async () => {
    const active = makeContainer({
      id: "c1",
      label: "TUBE-B",
      current_location: makeLocation({ id: "l-fridge", code: "FRIDGE", name: "冷藏冰箱", is_cold_storage: true }),
    });
    const batch = makeBatch({ containers: [active] });
    const locations = [
      makeLocation({ id: "l-fridge", code: "FRIDGE", name: "冷藏冰箱", is_cold_storage: true }),
      makeLocation({ id: "l-bench", code: "BENCH", name: "处理台", is_cold_storage: false }),
    ];
    const created: Handoff = makeHandoff(active, { container_id: active.id, container_label: "TUBE-B" });
    const spy = vi.spyOn(api, "createHandoff").mockResolvedValue(created);
    const onCreated = vi.fn();
    render(<NewHandoffForm batches={[batch]} locations={locations} onCreated={onCreated} />);

    fireEvent.click(screen.getByRole("button", { name: "发起交接" }));
    fireEvent.change(screen.getByLabelText("容器"), { target: { value: active.id } });
    fireEvent.change(screen.getByLabelText("目的位置"), { target: { value: "BENCH" } });
    fireEvent.change(screen.getByLabelText("发起人"), { target: { value: "alice" } });
    fireEvent.click(screen.getByRole("button", { name: "生成一次性接收码" }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy.mock.calls[0][0].container_id).toBe("c1");
    expect(spy.mock.calls[0][0].from_location_code).toBe("FRIDGE");
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(created));
  });
});
