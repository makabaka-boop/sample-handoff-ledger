import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";
import {
  TemperatureLog,
  TemperatureObservationForm,
  defaultObservedAt,
  localInputToUtcIso,
  utcIsoToLocalInput,
} from "./temperature";
import { makeBatch, makeContainer, makeObservation } from "./test/fixtures";
import type { Batch } from "./types";

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

function openForm() {
  fireEvent.click(screen.getByRole("button", { name: "补录人工测温" }));
}

describe("temperature observation form", () => {
  it("posts the reading and hands back the refreshed batch on success", async () => {
    const container = makeContainer();
    const batch = makeBatch({ containers: [container] });
    const refreshed: Batch = makeBatch({
      containers: [container],
      temperature_observations: [
        makeObservation(batch.id, container.id, {
          temperature_c: 5.2,
          verdict: "normal",
          measured_by: "night-a",
        }),
      ],
    });
    const spy = vi.spyOn(api, "recordTemperature").mockResolvedValue(refreshed);
    const onRecorded = vi.fn();
    render(
      <TemperatureObservationForm container={container} batch={batch} onRecorded={onRecorded} />,
    );

    openForm();
    fireEvent.change(screen.getByLabelText("摄氏温度 (°C)"), { target: { value: "5.2" } });
    fireEvent.change(screen.getByLabelText("测量人"), { target: { value: "night-a" } });
    fireEvent.change(screen.getByLabelText("测量时间"), {
      target: { value: "2026-09-10T17:30" },
    });
    fireEvent.change(screen.getByLabelText("备注"), { target: { value: "交接窗复测" } });
    fireEvent.click(screen.getByRole("button", { name: "提交测温判定" }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    const [containerId, payload] = spy.mock.calls[0];
    expect(containerId).toBe(container.id);
    expect(payload.temperature_c).toBe(5.2);
    expect(payload.measured_by).toBe("night-a");
    // The local datetime is sent as an explicit UTC instant.
    expect(payload.observed_at).toBe(localInputToUtcIso("2026-09-10T17:30"));
    expect(payload.observed_at).toMatch(/2026-09-10T\d{2}:\d{2}:00\.000Z/);
    expect(payload.note).toBe("交接窗复测");
    await waitFor(() => expect(onRecorded).toHaveBeenCalledWith(refreshed));
    expect(screen.queryByRole("button", { name: "提交测温判定" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "补录人工测温" })).toBeInTheDocument();
  });

  it("keeps every input and shows the mapped error when adjudication fails", async () => {
    const container = makeContainer();
    const batch = makeBatch({ containers: [container] });
    vi.spyOn(api, "recordTemperature").mockRejectedValue(
      new ApiError("INVALID_OBSERVED_AT", "bad time", false, "trace-temp"),
    );
    render(
      <TemperatureObservationForm
        container={container}
        batch={batch}
        onRecorded={() => {}}
      />,
    );

    openForm();
    fireEvent.change(screen.getByLabelText("摄氏温度 (°C)"), { target: { value: "14.0" } });
    fireEvent.change(screen.getByLabelText("测量人"), { target: { value: "night-a" } });
    fireEvent.change(screen.getByLabelText("测量时间"), {
      target: { value: "2026-09-10T17:30" },
    });
    fireEvent.change(screen.getByLabelText("备注"), { target: { value: "稍后改时间重试" } });
    fireEvent.click(screen.getByRole("button", { name: "提交测温判定" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("测量时间无效"));
    expect(screen.getByRole("alert")).toHaveTextContent("trace-temp");
    expect(screen.getByLabelText("摄氏温度 (°C)")).toHaveValue(14);
    expect(screen.getByLabelText("测量人")).toHaveValue("night-a");
    expect(screen.getByLabelText("测量时间")).toHaveValue("2026-09-10T17:30");
    expect(screen.getByLabelText("备注")).toHaveValue("稍后改时间重试");
  });

  it("sends null for a blank note", async () => {
    const container = makeContainer();
    const batch = makeBatch({ containers: [container] });
    const spy = vi.spyOn(api, "recordTemperature").mockResolvedValue(batch);
    render(
      <TemperatureObservationForm
        container={container}
        batch={batch}
        onRecorded={() => {}}
      />,
    );
    openForm();
    fireEvent.change(screen.getByLabelText("摄氏温度 (°C)"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("测量人"), { target: { value: "a" } });
    fireEvent.change(screen.getByLabelText("备注"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "提交测温判定" }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy.mock.calls[0][1].note).toBeNull();
  });

  it("defaults the measurement time to now so submitting right after batch creation works", async () => {
    const container = makeContainer();
    // Batch created moments ago: the prior minute-rounded default predated it.
    const batch = makeBatch({
      containers: [container],
      created_at: new Date(Date.now() - 2000).toISOString(),
    });
    const spy = vi.spyOn(api, "recordTemperature").mockResolvedValue(batch);
    render(
      <TemperatureObservationForm
        container={container}
        batch={batch}
        onRecorded={() => {}}
      />,
    );
    openForm();
    fireEvent.change(screen.getByLabelText("摄氏温度 (°C)"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("测量人"), { target: { value: "night-a" } });
    fireEvent.click(screen.getByRole("button", { name: "提交测温判定" }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    const observedAt = new Date(spy.mock.calls[0][1].observed_at).getTime();
    expect(observedAt).toBeGreaterThanOrEqual(new Date(batch.created_at).getTime());
    expect(observedAt).toBeLessThanOrEqual(Date.now() + 1000);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("temperature log", () => {
  it("renders newest first with temperature, verdict, measurer and time", () => {
    const container = makeContainer();
    const batch = makeBatch({
      containers: [container],
      temperature_observations: [
        makeObservation("batch-fixed", container.id, {
          id: "o2",
          temperature_c: 9.5,
          verdict: "out_of_range",
          measured_by: "night-b",
          observed_at: "2026-09-10T09:30:00Z",
        }),
        makeObservation("batch-fixed", container.id, {
          id: "o1",
          temperature_c: 5,
          verdict: "normal",
          measured_by: "night-a",
          observed_at: "2026-09-10T08:00:00Z",
        }),
      ],
    });
    render(<TemperatureLog observations={batch.temperature_observations ?? []} />);

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    // API ordering is newest first; the UI preserves it.
    expect(items[0]).toHaveTextContent("9.5°C");
    expect(items[0]).toHaveTextContent("越界");
    expect(items[0]).toHaveTextContent("night-b");
    expect(items[1]).toHaveTextContent("5°C");
    expect(items[1]).toHaveTextContent("正常");
    expect(items[1]).toHaveTextContent("night-a");
    expect(screen.getByText("9.5°C")).toBeInTheDocument();
  });

  it("shows an empty-state hint before the first reading", () => {
    render(<TemperatureLog observations={[]} />);
    expect(screen.getByText(/尚无人工测温记录/)).toBeInTheDocument();
  });
});

describe("datetime local helpers", () => {
  it("round-trips a UTC instant through local input format", () => {
    const iso = "2026-09-10T09:30:00Z";
    const local = utcIsoToLocalInput(iso);
    expect(new Date(localInputToUtcIso(local)).getTime()).toBe(new Date(iso).getTime());
  });

  it("defaults to the current instant with second precision", () => {
    const before = new Date();
    const value = defaultObservedAt();
    const after = new Date();
    expect(value).toMatch(/T\d{2}:\d{2}:\d{2}$/);
    const parsed = new Date(localInputToUtcIso(value));
    expect(parsed.getTime()).toBeGreaterThanOrEqual(before.getTime() - 1000);
    expect(parsed.getTime()).toBeLessThanOrEqual(after.getTime() + 1000);
  });

  it("clamps the default to batch creation so an immediate reading validates", () => {
    // Simulate a batch created slightly in the future (clock skew / same-minute
    // rounding): the default must never be earlier than the batch itself.
    const futureBatch = new Date(Date.now() + 30_000).toISOString();
    const value = defaultObservedAt(futureBatch);
    expect(new Date(localInputToUtcIso(value)).getTime()).toBeGreaterThanOrEqual(
      new Date(futureBatch).getTime() - 1000,
    );
  });
});
