import { expect, test } from "@playwright/test";

const API_URL = process.env.E2E_API_URL ?? "http://localhost:8000/api";

async function createTemperatureBatch(
  request: import("@playwright/test").APIRequestContext,
  suffix: string,
) {
  const response = await request.post(`${API_URL}/batches`, {
    data: {
      accession_number: `TEMP-${suffix}`,
      temp_min_c: 2,
      temp_max_c: 8,
      max_out_minutes: 30,
      created_by: "alice",
      containers: [{ label: "TEMP-TUBE", initial_location_code: "FRIDGE" }],
    },
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

function localInputMinutesAgo(minutes: number): string {
  const date = new Date(Date.now() - minutes * 60_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

async function submitReading(page: import("@playwright/test").Page, value: string, minutesAgo: number) {
  await page.getByRole("button", { name: "补录人工测温" }).first().click();
  await page.getByLabel("摄氏温度 (°C)").fill(value);
  await page.getByLabel("测量人").fill("night-a");
  await page.getByLabel("测量时间").fill(localInputMinutesAgo(minutesAgo));
  await page.getByLabel("备注").fill("夜班交接窗复测");
  await page.getByRole("button", { name: "提交测温判定" }).click();
}

test("an in-range reading is normal, keeps the batch active and writes one timeline event", async ({
  page,
  request,
}) => {
  const suffix = Date.now().toString();
  const batch = await createTemperatureBatch(request, suffix);

  await page.goto("/");
  await page.getByRole("button", { name: "批次档案" }).click();
  await page.getByRole("button", { name: `TEMP-${suffix}` }).click();

  await submitReading(page, "5.0", 10);
  await expect(page.getByText("人工测温已按批次温区判定")).toBeVisible();

  const detailResponse = await request.get(`${API_URL}/batches/${batch.id}`);
  expect(detailResponse.ok()).toBeTruthy();
  const detail = await detailResponse.json();
  expect(detail.disposition).toBe("active");
  expect(detail.temperature_observations).toHaveLength(1);
  expect(detail.temperature_observations[0].verdict).toBe("normal");
  expect(detail.temperature_observations[0].temperature_c).toBe(5);
  expect(detail.temperature_observations[0].measured_by).toBe("night-a");
  // Location and exposure timing are untouched by a temperature reading.
  expect(detail.containers[0].current_location.code).toBe("FRIDGE");
  expect(detail.containers[0].accumulated_out_seconds).toBe(0);

  const events = detail.timeline.filter(
    (event: { event_type: string }) => event.event_type === "temperature_recorded",
  );
  expect(events).toHaveLength(1);
  expect(events[0].container_id).toBe(batch.containers[0].id);
  expect(events[0].details.verdict).toBe("normal");
});

test("an out-of-range reading enters review and still produces exactly one event", async ({
  page,
  request,
}) => {
  const suffix = (Date.now() + 1).toString();
  const batch = await createTemperatureBatch(request, suffix);

  await page.goto("/");
  await page.getByRole("button", { name: "批次档案" }).click();
  await page.getByRole("button", { name: `TEMP-${suffix}` }).click();

  await submitReading(page, "9.5", 5);
  await expect(page.getByText("人工测温已按批次温区判定")).toBeVisible();

  const detailResponse = await request.get(`${API_URL}/batches/${batch.id}`);
  const detail = await detailResponse.json();
  expect(detail.disposition).toBe("review");
  expect(detail.temperature_observations).toHaveLength(1);
  expect(detail.temperature_observations[0].verdict).toBe("out_of_range");
  const events = detail.timeline.filter(
    (event: { event_type: string }) => event.event_type === "temperature_recorded",
  );
  expect(events).toHaveLength(1);
  expect(events[0].details.verdict).toBe("out_of_range");
  // The container itself never moved.
  expect(detail.containers[0].current_location.code).toBe("FRIDGE");
});
