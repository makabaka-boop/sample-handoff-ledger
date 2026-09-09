import { expect, test } from "@playwright/test";

const API_URL = process.env.E2E_API_URL ?? "http://localhost:8000/api";

async function createRejectableBatch(request: import("@playwright/test").APIRequestContext, suffix: string) {
  const batchResponse = await request.post(`${API_URL}/batches`, {
    data: {
      accession_number: `REJECT-${suffix}`,
      temperature_zone: "2–8°C",
      max_out_minutes: 30,
      created_by: "alice",
      containers: [{ label: "REJECT-TUBE", initial_location_code: "FRIDGE" }],
    },
  });
  expect(batchResponse.ok()).toBeTruthy();
  const batch = await batchResponse.json();
  const handoffResponse = await request.post(`${API_URL}/handoffs`, {
    data: {
      container_id: batch.containers[0].id,
      from_location_code: "FRIDGE",
      to_location_code: "BENCH",
      created_by: "alice",
      ttl_minutes: 10,
    },
  });
  expect(handoffResponse.ok()).toBeTruthy();
  const handoff = await handoffResponse.json();
  return { batch, handoff };
}

test("receiver rejection keeps location, sends batch to review, writes one timeline event", async ({
  page,
  request,
}) => {
  const suffix = Date.now().toString();
  const { batch, handoff } = await createRejectableBatch(request, suffix);

  await page.goto("/");
  await page.getByRole("button", { name: "短码接收" }).click();
  await page.getByRole("radio", { name: "拒绝接收" }).click();
  await page.getByLabel("接收码").fill(handoff.receipt_code);
  await page.getByLabel("拒收人").fill("night-lead");
  await page.getByLabel("拒收原因").selectOption("seal_broken");
  await page.getByLabel("备注").fill("封签撕毁，拒收");
  await page.getByRole("button", { name: "拒绝接收并形成异常" }).click();
  await expect(page.getByRole("alert")).toContainText("批次进入复核");

  // Read the adjudication straight back from the real API/PostgreSQL.
  const detailResponse = await request.get(`${API_URL}/batches/${batch.id}`);
  expect(detailResponse.ok()).toBeTruthy();
  const detail = await detailResponse.json();
  expect(detail.containers[0].current_location.code).toBe("FRIDGE");
  expect(detail.disposition).toBe("review");
  expect(detail.has_unresolved_anomaly).toBeTruthy();
  const rejected = detail.timeline.filter(
    (event: { event_type: string }) => event.event_type === "handoff_rejected",
  );
  expect(rejected).toHaveLength(1);
  expect(rejected[0].details.reason).toBe("seal_broken");
  expect(rejected[0].note).toBe("封签撕毁，拒收");

  // Re-submitting the same code replays the first rejection and adds no event.
  await page.getByLabel("接收码").fill(handoff.receipt_code);
  await page.getByLabel("拒收人").fill("someone-else");
  await page.getByRole("button", { name: "拒绝接收并形成异常" }).click();
  await expect(page.getByRole("alert")).toContainText("早前拒收仍然有效");
  const replayedDetail = await (await request.get(`${API_URL}/batches/${batch.id}`)).json();
  expect(
    replayedDetail.timeline.filter(
      (event: { event_type: string }) => event.event_type === "handoff_rejected",
    ),
  ).toHaveLength(1);

  // The detail drawer shows the rejection fact; reopening issues a fresh code.
  await page.getByRole("button", { name: "交接待办" }).click();
  await page.getByText(`REJECT-${suffix}`).first().click();
  await expect(page.getByText("RECEIVER_REJECTED")).toBeVisible();
  await expect(page.getByText("容器封签破损")).toBeVisible();
  await expect(page.getByText("封签撕毁，拒收").first()).toBeVisible();

  await page.getByPlaceholder("操作人").fill("dana");
  const [reopenResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/reopen") && response.ok()),
    page.getByRole("button", { name: "重开交接" }).click(),
  ]);
  const reopened = await reopenResponse.json();
  expect(reopened.id).not.toBe(handoff.id);
  expect(reopened.receipt_code).toMatch(/^\d{6}$/);
  await expect(page.getByText("新接收码")).toBeVisible();

  const afterReopen = await (await request.get(`${API_URL}/batches/${batch.id}`)).json();
  expect(afterReopen.disposition).toBe("active");
  expect(afterReopen.containers[0].current_location.code).toBe("FRIDGE");
});
