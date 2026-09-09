import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReceivePanel } from "./App";

describe("receipt retry behavior", () => {
  afterEach(() => vi.restoreAllMocks());

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
});
