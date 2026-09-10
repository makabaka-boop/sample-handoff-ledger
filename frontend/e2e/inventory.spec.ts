import { expect, test } from "@playwright/test";

const API_URL = process.env.E2E_API_URL ?? "http://localhost:8000/api";

async function createColdLocation(
  request: import("@playwright/test").APIRequestContext,
  suffix: string,
) {
  // A dedicated cold location per test keeps the book snapshot free of the
  // other spec's containers (Playwright runs files in parallel workers).
  const response = await request.post(`${API_URL}/locations`, {
    data: {
      code: `FRZ${suffix}`.slice(0, 40),
      name: `盘点冷柜 ${suffix}`,
      is_cold_storage: true,
    },
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function createBatch(
  request: import("@playwright/test").APIRequestContext,
  accession: string,
  labels: string[],
  locationCode: string,
) {
  const response = await request.post(`${API_URL}/batches`, {
    data: {
      accession_number: accession,
      temp_min_c: 2,
      temp_max_c: 8,
      max_out_minutes: 30,
      created_by: "alice",
      containers: labels.map((label) => ({
        label,
        initial_location_code: locationCode,
      })),
    },
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function moveToBench(
  request: import("@playwright/test").APIRequestContext,
  batch: { containers: { id: string; current_location: { code: string } }[] },
) {
  const handoffResponse = await request.post(`${API_URL}/handoffs`, {
    data: {
      container_id: batch.containers[0].id,
      from_location_code: batch.containers[0].current_location.code,
      to_location_code: "BENCH",
      created_by: "alice",
      ttl_minutes: 10,
    },
  });
  expect(handoffResponse.ok()).toBeTruthy();
  const handoff = await handoffResponse.json();
  const confirm = await request.post(`${API_URL}/handoffs/confirm`, {
    data: { code: handoff.receipt_code, received_by: "bob" },
  });
  expect(confirm.ok()).toBeTruthy();
}

test("cold location recount is classified into one snapshot without moving custody", async ({
  page,
  request,
}) => {
  const suffix = Date.now().toString();
  const location = await createColdLocation(request, suffix);
  const onShelf = await createBatch(
    request,
    `INV-E2E-A-${suffix}`,
    ["INV-A1", "INV-A2"],
    location.code,
  );
  const strayBatch = await createBatch(
    request,
    `INV-E2E-B-${suffix}`,
    ["INV-B1"],
    location.code,
  );
  await moveToBench(request, strayBatch);

  await page.goto("/");
  await page.getByRole("button", { name: "位置盘点" }).click();
  await expect(page.getByRole("heading", { name: "位置盘点" })).toBeVisible();
  await page.getByLabel("冷藏位置").selectOption({ label: `${location.name}（${location.code}）` });
  await page.getByLabel("盘点人").fill("night-a");
  // Scan: one match, one physically-stray container, one unknown label; INV-A2
  // stays on the book but is not scanned, so it must come back as 账面缺失.
  await page
    .getByLabel("容器标签（每行一个，按扫描顺序）")
    .fill("INV-A1\nINV-B1\nGHOST-TAG");
  await page.getByRole("button", { name: "提交盘点" }).click();

  await expect(page.getByRole("heading", { name: "本次盘点结果" })).toBeVisible();
  await expect(page.getByTestId("count-matched")).toHaveText("1");
  await expect(page.getByTestId("count-missing")).toHaveText("1");
  await expect(page.getByTestId("count-misplaced")).toHaveText("1");
  await expect(page.getByTestId("count-unknown")).toHaveText("1");
  await expect(page.getByText(/系统记录位置：处理台（BENCH）/)).toBeVisible();

  // Read the adjudicated evidence straight back from the real API/database.
  const checksResponse = await request.get(
    `${API_URL}/locations/${location.id}/inventory-checks`,
  );
  expect(checksResponse.ok()).toBeTruthy();
  const checks = await checksResponse.json();
  expect(checks).toHaveLength(1);
  const check = checks[0];
  expect(check.checked_by).toBe("night-a");
  expect(check.scanned_count).toBe(3);
  expect(check.matched_count).toBe(1);
  expect(check.missing_count).toBe(1);
  expect(check.misplaced_count).toBe(1);
  expect(check.unknown_count).toBe(1);

  const detailResponse = await request.get(`${API_URL}/inventory-checks/${check.id}`);
  const detail = await detailResponse.json();
  const byLabel = Object.fromEntries(
    detail.items.map(
      (item: { scanned_label: string }) => [item.scanned_label, item],
    ),
  );
  expect(byLabel["INV-A1"].category).toBe("matched");
  expect(byLabel["INV-B1"].category).toBe("misplaced");
  expect(byLabel["INV-B1"].recorded_location_code).toBe("BENCH");
  expect(byLabel["GHOST-TAG"].category).toBe("unknown");
  const missing = detail.items.find(
    (item: { category: string }) => item.category === "missing",
  );
  expect(missing.container_label).toBe("INV-A2");

  // Exactly one snapshot exists for this submission, with one row per finding.
  expect(detail.items).toHaveLength(4);

  // The recount is evidence only: positions, dispositions and the chain of
  // custody are exactly as the handoffs left them.
  const shelfDetail = await (await request.get(`${API_URL}/batches/${onShelf.id}`)).json();
  const strayDetail = await (
    await request.get(`${API_URL}/batches/${strayBatch.id}`)
  ).json();
  expect(shelfDetail.disposition).toBe("active");
  expect(strayDetail.disposition).toBe("active");
  expect(
    shelfDetail.containers.map(
      (c: { label: string; current_location: { code: string } }) => [
        c.label,
        c.current_location.code,
      ],
    ),
  ).toEqual([
    ["INV-A1", location.code],
    ["INV-A2", location.code],
  ]);
  expect(strayDetail.containers[0].current_location.code).toBe("BENCH");

  const custodyEvents = (batch: { timeline: { event_type: string }[] }) =>
    batch.timeline.map((event) => event.event_type);
  expect(new Set(custodyEvents(shelfDetail))).toEqual(
    new Set(["container_registered", "batch_created"]),
  );
  expect(new Set(custodyEvents(strayDetail))).toEqual(
    new Set(["container_registered", "batch_created", "handoff_initiated", "handoff_received"]),
  );

  // The recent-records list refreshes under the result after success.
  await expect(page.getByText(/最近盘点记录/)).toBeVisible();
});

test("a fully matching recount reports four zero differences", async ({ page, request }) => {
  const suffix = (Date.now() + 1).toString();
  const location = await createColdLocation(request, suffix);
  const batch = await createBatch(
    request,
    `INV-E2E-C-${suffix}`,
    ["INV-C1"],
    location.code,
  );

  await page.goto("/");
  await page.getByRole("button", { name: "位置盘点" }).click();
  await page.getByLabel("冷藏位置").selectOption({ label: `${location.name}（${location.code}）` });
  await page.getByLabel("盘点人").fill("night-b");
  await page.getByLabel("容器标签（每行一个，按扫描顺序）").fill("INV-C1");
  await page.getByRole("button", { name: "提交盘点" }).click();

  await expect(page.getByTestId("count-matched")).toHaveText("1");
  await expect(page.getByTestId("count-missing")).toHaveText("0");
  await expect(page.getByTestId("count-misplaced")).toHaveText("0");
  await expect(page.getByTestId("count-unknown")).toHaveText("0");
  await expect(page.getByText("账面在柜、扫描未见")).not.toBeVisible();

  const checks = await (
    await request.get(`${API_URL}/locations/${location.id}/inventory-checks`)
  ).json();
  expect(checks).toHaveLength(1);
  expect(checks[0].matched_count).toBe(1);

  const detail = await (await request.get(`${API_URL}/batches/${batch.id}`)).json();
  expect(detail.containers[0].current_location.code).toBe(location.code);
  expect(detail.disposition).toBe("active");
});

test("blank lines inside or at the end of the scan are rejected with no record", async ({
  page,
  request,
}) => {
  const suffix = (Date.now() + 2).toString();
  const location = await createColdLocation(request, suffix);
  await createBatch(request, `INV-E2E-D-${suffix}`, ["INV-D1"], location.code);

  await page.goto("/");
  await page.getByRole("button", { name: "位置盘点" }).click();
  await page.getByLabel("冷藏位置").selectOption({ label: `${location.name}（${location.code}）` });
  await page.getByLabel("盘点人").fill("night-c");

  async function assertRejected(rawScan: string, lineNumbers: string) {
    await page
      .getByLabel("容器标签（每行一个，按扫描顺序）")
      .fill(rawScan);
    await page.getByRole("button", { name: "提交盘点" }).click();
    await expect(page.getByRole("alert")).toContainText("标签清单无效");
    await expect(page.getByRole("alert")).toContainText(lineNumbers);
    // The raw scan stays in the form so the operator can remove the blank line.
    await expect(page.getByLabel("容器标签（每行一个，按扫描顺序）")).toHaveValue(
      rawScan,
    );
    await expect(page.getByRole("heading", { name: "本次盘点结果" })).toHaveCount(0);
  }

  // A scanner gap between two reads must be reported as an invalid label, not
  // silently dropped and submitted (the duplicate label is irrelevant while
  // the blank-line error blocks the request first).
  await assertRejected("INV-D1\n   \nINV-D1", "第 2 行");

  // A stray carriage return after the final label leaves a trailing blank
  // line: this is invalid as well, never auto-submitted.
  await assertRejected("INV-D1\n", "第 2 行");

  // Nothing reached the server: no record exists for this location.
  const checksResponse = await request.get(
    `${API_URL}/locations/${location.id}/inventory-checks`,
  );
  expect(await checksResponse.json()).toHaveLength(0);

  // Once every line contains a label, the recount submits normally.
  await page
    .getByLabel("容器标签（每行一个，按扫描顺序）")
    .fill("INV-D1");
  await page.getByRole("button", { name: "提交盘点" }).click();
  await expect(page.getByTestId("count-matched")).toHaveText("1");
  const checks = await (
    await request.get(`${API_URL}/locations/${location.id}/inventory-checks`)
  ).json();
  expect(checks).toHaveLength(1);
});
