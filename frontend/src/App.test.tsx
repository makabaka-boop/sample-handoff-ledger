import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReceivePanel } from "./App";
import { api, ApiError } from "./api";
import type { Handoff } from "./types";

const rejectedHandoff = {
  status: "anomaly",
  replayed: false,
  container_label: "TUBE-A",
  from_location: { code: "FRIDGE", name: "冷藏冰箱" },
  to_location: { code: "BENCH", name: "处理台" },
} as Handoff;

function switchToReject() {
  fireEvent.click(screen.getByRole("radio", { name: "拒绝接收" }));
}

describe("receipt retry behavior", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    cleanup();
  });

  it("keeps both inputs after a network failure and does not report success", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("offline"));
    const onDone = vi.fn();
    render(<ReceivePanel onDone={onDone} />);

    fireEvent.change(screen.getByLabelText("接收码"), { target: { value: "123456" } });
    fireEvent.change(screen.getByLabelText("接收人"), { target: { value: "night-operator" } });
    fireEvent.click(screen.getByRole("button", { name: "确认接收" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("输入已保留"));
    expect(screen.getByLabelText("接收码")).toHaveValue("123456");
    expect(screen.getByLabelText("接收人")).toHaveValue("night-operator");
    expect(onDone).not.toHaveBeenCalled();
  });

  it("keeps code, rejecter, reason and note when a rejection fails offline", async () => {
    vi.spyOn(api, "reject").mockRejectedValue(new TypeError("offline"));
    const onDone = vi.fn();
    render(<ReceivePanel onDone={onDone} />);
    switchToReject();

    fireEvent.change(screen.getByLabelText("接收码"), { target: { value: "654321" } });
    fireEvent.change(screen.getByLabelText("拒收人"), { target: { value: "night-lead" } });
    fireEvent.change(screen.getByLabelText("拒收原因"), {
      target: { value: "package_contaminated" },
    });
    fireEvent.change(screen.getByLabelText("备注"), { target: { value: "封条断裂、外壁残留" } });
    fireEvent.click(screen.getByRole("button", { name: "拒绝接收并形成异常" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("输入已保留"));
    expect(screen.getByLabelText("接收码")).toHaveValue("654321");
    expect(screen.getByLabelText("拒收人")).toHaveValue("night-lead");
    expect(screen.getByLabelText("拒收原因")).toHaveValue("package_contaminated");
    expect(screen.getByLabelText("备注")).toHaveValue("封条断裂、外壁残留");
    expect(screen.getByRole("radio", { name: "拒绝接收" })).toBeChecked();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("posts the rejection payload, then clears the form and refreshes todos", async () => {
    const rejectSpy = vi.spyOn(api, "reject").mockResolvedValue(rejectedHandoff);
    const onDone = vi.fn();
    render(<ReceivePanel onDone={onDone} />);
    switchToReject();

    fireEvent.change(screen.getByLabelText("接收码"), { target: { value: "246810" } });
    fireEvent.change(screen.getByLabelText("拒收人"), { target: { value: "night-lead" } });
    fireEvent.change(screen.getByLabelText("拒收原因"), { target: { value: "seal_broken" } });
    fireEvent.change(screen.getByLabelText("备注"), { target: { value: "封签撕毁" } });
    fireEvent.click(screen.getByRole("button", { name: "拒绝接收并形成异常" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(rejectSpy).toHaveBeenCalledWith(
      "246810",
      "night-lead",
      "seal_broken",
      "封签撕毁",
    );
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("留在冷藏冰箱"));
    expect(screen.getByRole("alert")).toHaveTextContent("复核");
    expect(screen.getByLabelText("接收码")).toHaveValue("");
    expect(screen.getByLabelText("备注")).toHaveValue("");
  });

  it("shows the mapped race error when confirmation loses to a rejection", async () => {
    vi.spyOn(api, "confirm").mockRejectedValue(
      new ApiError("HANDOFF_REJECTED", "lost", false, "trace-77"),
    );
    const onDone = vi.fn();
    render(<ReceivePanel onDone={onDone} />);

    fireEvent.change(screen.getByLabelText("接收码"), { target: { value: "135790" } });
    fireEvent.change(screen.getByLabelText("接收人"), { target: { value: "bob" } });
    fireEvent.click(screen.getByRole("button", { name: "确认接收" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("该交接已被夜班拒收"),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("trace-77");
    expect(screen.getByLabelText("接收码")).toHaveValue("135790");
    expect(onDone).not.toHaveBeenCalled();
  });
});
