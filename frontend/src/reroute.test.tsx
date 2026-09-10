import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DetailDrawer, RerouteForm } from "./App";
import { api, ApiError } from "./api";
import { makeContainer, makeHandoff, makeLocation } from "./test/fixtures";
import type { Handoff, Location } from "./types";

const fridge = makeLocation({ id: "loc-fridge", code: "FRIDGE", name: "冷藏冰箱" });
const bench = makeLocation({ id: "loc-bench", code: "BENCH", name: "处理台", is_cold_storage: false });
const window_ = makeLocation({ id: "loc-window", code: "WINDOW", name: "交接窗", is_cold_storage: false });
const coldroom = makeLocation({ id: "loc-coldroom", code: "COLDROOM", name: "备用冷藏间" });
const locations: Location[] = [fridge, bench, window_, coldroom];

function pendingHandoff(overrides: Partial<Handoff> = {}): Handoff {
  return makeHandoff(makeContainer(), {
    id: "handoff-1",
    from_location: fridge,
    to_location: bench,
    remaining_seconds: 600,
    ...overrides,
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

describe("reroute form", () => {
  it("offers only same-environment positions and posts the reroute payload", async () => {
    const handoff = pendingHandoff();
    const rerouted = pendingHandoff({
      to_location: window_,
      original_to_location: bench,
      rerouted_by: "alice",
      rerouted_at: "2026-09-10T08:03:00Z",
      reroute_reason: "处理台临时停用",
    });
    const spy = vi.spyOn(api, "reroute").mockResolvedValue(rerouted);
    const onRerouted = vi.fn();
    render(<RerouteForm handoff={handoff} locations={locations} onRerouted={onRerouted} />);

    const select = screen.getByLabelText("新目标位置");
    const options = Array.from(select.querySelectorAll("option")).map((option) => option.textContent);
    // Same cold-storage class only: no FRIDGE (source), no BENCH (current), no COLDROOM.
    expect(options).toEqual(["请选择", "交接窗"]);

    fireEvent.change(select, { target: { value: "WINDOW" } });
    fireEvent.change(screen.getByLabelText("改派人"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("改派原因"), { target: { value: "处理台临时停用" } });
    fireEvent.click(screen.getByRole("button", { name: "提交改派" }));

    await waitFor(() => expect(onRerouted).toHaveBeenCalledWith(rerouted));
    expect(spy).toHaveBeenCalledWith("handoff-1", {
      actor: "alice",
      to_location_code: "WINDOW",
      reason: "处理台临时停用",
    });
  });

  it("keeps the operator, reason and chosen position when the submission fails", async () => {
    vi.spyOn(api, "reroute").mockRejectedValue(
      new ApiError("REROUTE_ENVIRONMENT_MISMATCH", "mismatch", false, "trace-5"),
    );
    const onRerouted = vi.fn();
    render(<RerouteForm handoff={pendingHandoff()} locations={locations} onRerouted={onRerouted} />);

    fireEvent.change(screen.getByLabelText("新目标位置"), { target: { value: "WINDOW" } });
    fireEvent.change(screen.getByLabelText("改派人"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("改派原因"), { target: { value: "处理台临时停用" } });
    fireEvent.click(screen.getByRole("button", { name: "提交改派" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("冷藏属性不一致"));
    expect(screen.getByLabelText("新目标位置")).toHaveValue("WINDOW");
    expect(screen.getByLabelText("改派人")).toHaveValue("alice");
    expect(screen.getByLabelText("改派原因")).toHaveValue("处理台临时停用");
    expect(onRerouted).not.toHaveBeenCalled();
  });

  it("keeps every input when the network drops mid-submission", async () => {
    vi.spyOn(api, "reroute").mockRejectedValue(new TypeError("offline"));
    render(<RerouteForm handoff={pendingHandoff()} locations={locations} onRerouted={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("新目标位置"), { target: { value: "WINDOW" } });
    fireEvent.change(screen.getByLabelText("改派人"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("改派原因"), { target: { value: "处理台临时停用" } });
    fireEvent.click(screen.getByRole("button", { name: "提交改派" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("输入已保留"));
    expect(screen.getByLabelText("新目标位置")).toHaveValue("WINDOW");
    expect(screen.getByLabelText("改派人")).toHaveValue("alice");
    expect(screen.getByLabelText("改派原因")).toHaveValue("处理台临时停用");
  });
});

describe("task detail reroute", () => {
  function Harness({ initial }: { initial: Handoff }) {
    const [handoff, setHandoff] = useState(initial);
    return (
      <DetailDrawer
        handoff={handoff}
        batch={null}
        locations={locations}
        close={() => {}}
        refresh={() => {}}
        reloadBatch={() => {}}
        showCode="123456"
        onRerouted={setHandoff}
      />
    );
  }

  it("shows 改派目标 for a valid pending record and refreshes the target without rebuilding the code or countdown", async () => {
    const handoff = pendingHandoff();
    const rerouted = pendingHandoff({
      to_location: window_,
      original_to_location: bench,
      rerouted_by: "alice",
      rerouted_at: "2026-09-10T08:03:00Z",
      reroute_reason: "处理台临时停用",
      // Same deadline, less remaining: the countdown continues, it is not rebuilt.
      remaining_seconds: 540,
    });
    vi.spyOn(api, "reroute").mockResolvedValue(rerouted);
    render(<Harness initial={handoff} />);

    expect(screen.getByText("改派目标")).toBeInTheDocument();
    expect(screen.getByText("123456")).toBeInTheDocument();
    expect(screen.getByText("600 秒")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("新目标位置"), { target: { value: "WINDOW" } });
    fireEvent.change(screen.getByLabelText("改派人"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("改派原因"), { target: { value: "处理台临时停用" } });
    fireEvent.click(screen.getByRole("button", { name: "提交改派" }));

    // The target refreshes immediately from the adjudicated response.
    await waitFor(() => expect(screen.getByText("交接窗")).toBeInTheDocument());
    // The previous target is kept on record as 原目标位置.
    expect(screen.getByText("原目标位置").nextElementSibling).toHaveTextContent("处理台");
    // The original code is still shown and no new code was issued.
    expect(screen.getByText("123456")).toBeInTheDocument();
    // The countdown continues from the same deadline instead of restarting at 600.
    expect(screen.getByText("540 秒")).toBeInTheDocument();
    expect(screen.queryByText("600 秒")).not.toBeInTheDocument();
  });

  it("hides 改派目标 once the handoff is no longer pending", () => {
    render(<Harness initial={pendingHandoff({ status: "received", persisted_status: "received" })} />);
    expect(screen.queryByText("改派目标")).not.toBeInTheDocument();
  });
});
