import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InventoryPanel, groupItems, parseScannedLabels } from "./inventory";
import { api, ApiError } from "./api";
import { makeInventoryItem, makeInventoryResult, makeLocation } from "./test/fixtures";

const fridge = makeLocation({ id: "loc-fridge", code: "FRIDGE", name: "冷藏冰箱" });
const bench = makeLocation({
  id: "loc-bench",
  code: "BENCH",
  name: "处理台",
  is_cold_storage: false,
});

function labels(): HTMLTextAreaElement {
  return screen.getByLabelText("容器标签（每行一个，按扫描顺序）");
}

function checker(): HTMLInputElement {
  return screen.getByLabelText("盘点人");
}

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

beforeEach(() => {
  vi.spyOn(api, "inventoryChecks").mockResolvedValue([]);
});

describe("label parsing helpers", () => {
  it("trims whitespace, drops blank lines and keeps scan order", () => {
    expect(parseScannedLabels(" TUBE-A \n\n  \nTUBE-B\n")).toEqual(["TUBE-A", "TUBE-B"]);
  });

  it("groups items into the four backend categories in display order", () => {
    const result = makeInventoryResult({
      matched_count: 1,
      missing_count: 1,
      misplaced_count: 1,
      unknown_count: 1,
      items: [
        makeInventoryItem({ id: "i-unknown", category: "unknown", scanned_label: "GHOST" }),
        makeInventoryItem({ id: "i-misplaced", category: "misplaced", scanned_label: "B1" }),
        makeInventoryItem({ id: "i-missing", category: "missing", scanned_label: null }),
        makeInventoryItem({ id: "i-matched", category: "matched", scanned_label: "A1" }),
      ],
    });
    expect(groupItems(result).map((group) => group.category)).toEqual([
      "matched",
      "missing",
      "misplaced",
      "unknown",
    ]);
  });
});

describe("inventory panel", () => {
  it("posts the scanned labels and checker, then shows the grouped result", async () => {
    const result = makeInventoryResult({
      matched_count: 1,
      missing_count: 1,
      misplaced_count: 1,
      unknown_count: 1,
      scanned_count: 3,
      items: [
        makeInventoryItem({ id: "i-matched", category: "matched", scanned_label: "A1" }),
        makeInventoryItem({
          id: "i-missing",
          category: "missing",
          scanned_label: null,
          container_label: "A2",
        }),
        makeInventoryItem({
          id: "i-misplaced",
          category: "misplaced",
          scanned_label: "B1",
          recorded_location_code: "BENCH",
          recorded_location_name: "处理台",
        }),
        makeInventoryItem({
          id: "i-unknown",
          category: "unknown",
          scanned_label: "GHOST",
          container_id: null,
          recorded_location_code: null,
        }),
      ],
    });
    const spy = vi.spyOn(api, "submitInventoryCheck").mockResolvedValue(result);
    render(<InventoryPanel locations={[fridge, bench]} />);

    fireEvent.change(checker(), { target: { value: "night-a" } });
    fireEvent.change(labels(), { target: { value: " A1 \nB1\nGHOST\n" } });
    fireEvent.click(screen.getByRole("button", { name: "提交盘点" }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith("loc-fridge", {
      checked_by: "night-a",
      labels: ["A1", "B1", "GHOST"],
    });

    expect(screen.getByTestId("count-matched")).toHaveTextContent("1");
    expect(screen.getByTestId("count-missing")).toHaveTextContent("1");
    expect(screen.getByTestId("count-misplaced")).toHaveTextContent("1");
    expect(screen.getByTestId("count-unknown")).toHaveTextContent("1");
    // Each category renders once as a count pill and once as a result group.
    for (const heading of ["已匹配", "账面缺失", "错放到此处", "未知标签"]) {
      expect(screen.getAllByText(heading).length).toBeGreaterThanOrEqual(2);
    }
    // The misplaced row carries the system-recorded location for correction.
    expect(screen.getByText(/系统记录位置：处理台（BENCH）/)).toBeInTheDocument();
    // Scanned labels clear after success; the checker stays for the next recount.
    expect(labels()).toHaveValue("");
    expect(checker()).toHaveValue("night-a");
  });

  it("keeps the labels and checker after an offline failure", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("offline"));
    render(<InventoryPanel locations={[fridge]} />);

    fireEvent.change(checker(), { target: { value: "night-b" } });
    fireEvent.change(labels(), { target: { value: "TUBE-A\nTUBE-B" } });
    fireEvent.click(screen.getByRole("button", { name: "提交盘点" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("输入已保留"));
    expect(labels()).toHaveValue("TUBE-A\nTUBE-B");
    expect(checker()).toHaveValue("night-b");
  });

  it("keeps the labels after INVALID_INVENTORY_LABELS and shows the mapped message", async () => {
    vi.spyOn(api, "submitInventoryCheck").mockRejectedValue(
      new ApiError("INVALID_INVENTORY_LABELS", "dup", false, "trace-inv"),
    );
    render(<InventoryPanel locations={[fridge]} />);

    fireEvent.change(checker(), { target: { value: "night-a" } });
    fireEvent.change(labels(), { target: { value: "TUBE-A\nTUBE-B" } });
    fireEvent.click(screen.getByRole("button", { name: "提交盘点" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("不能包含空白或重复标签"),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("trace-inv");
    expect(labels()).toHaveValue("TUBE-A\nTUBE-B");
    expect(checker()).toHaveValue("night-a");
  });

  it("blocks duplicate labels client-side before any request", async () => {
    const spy = vi.spyOn(api, "submitInventoryCheck");
    render(<InventoryPanel locations={[fridge]} />);

    fireEvent.change(checker(), { target: { value: "night-a" } });
    fireEvent.change(labels(), { target: { value: "TUBE-A\ntube-a" } });
    fireEvent.click(screen.getByRole("button", { name: "提交盘点" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("重复"));
    expect(spy).not.toHaveBeenCalled();
    expect(labels()).toHaveValue("TUBE-A\ntube-a");
  });

  it("offers only cold-storage locations", () => {
    render(<InventoryPanel locations={[fridge, bench]} />);
    const options = screen.getByLabelText("冷藏位置").querySelectorAll("option");
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveValue("loc-fridge");
  });

  it("loads the location's recent checks when mounted", async () => {
    const previous = {
      ...makeInventoryResult(),
      id: "check-old",
      checked_by: "night-old",
      matched_count: 4,
    };
    vi.spyOn(api, "inventoryChecks").mockResolvedValue([previous]);
    render(<InventoryPanel locations={[fridge]} />);

    await waitFor(() => expect(screen.getByText(/最近盘点记录/)).toBeInTheDocument());
    expect(screen.getByText(/盘点人 night-old/)).toBeInTheDocument();
  });
});
