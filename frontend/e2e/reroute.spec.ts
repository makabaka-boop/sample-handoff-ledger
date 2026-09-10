import { expect, test } from "@playwright/test";

const API_URL = process.env.E2E_API_URL ?? "http://localhost:8000/api";

async function createBatch(request: import("@playwright/test").APIRequestContext, suffix: string) {
  const response = await request.post(`${API_URL}/batches`, {
    data: {
      accession_number: `REROUTE-${suffix}`,
      temperature_zone: "2–8°C",
      max_out_minutes: 30,
      created_by: "alice",
      containers: [{ label: "REROUTE-TUBE", initial_location_code: "FRIDGE" }],
    },
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

test("initiator reroutes a pending handoff and the original code receives at the new target", async ({
  page,
  request,
}) => {
  const suffix = Date.now().toString();
  const batch = await createBatch(request, suffix);

  // Create the handoff through the browser so the one-time code is displayed.
  await page.goto("/");
  await page.getByRole("button", { name: "发起交接" }).click();
  await page.getByLabel("容器").selectOption({ label: `REROUTE-${suffix} · REROUTE-TUBE（冷藏冰箱）` });
  await page.getByLabel("目的位置").selectOption("BENCH");
  await page.getByLabel("有效期（分钟）").fill("10");
  await page.getByLabel("发起人").fill("alice");
  await page.getByRole("button", { name: "生成一次性接收码" }).click();
  const code = (await page.locator(".receipt-code strong").innerText()).trim();
  expect(code).toMatch(/^\d{6}$/);

  // The valid pending record offers 改派目标 in the detail drawer.
  await page.getByLabel("新目标位置").selectOption("WINDOW");
  await page.getByLabel("改派人").fill("alice");
  await page.getByLabel("改派原因").fill("处理台临时停用");
  await page.getByRole("button", { name: "提交改派" }).click();

  // The target refreshes immediately and the original code is not rebuilt.
  await expect(page.locator(".large-route")).toContainText("交接窗");
  await expect(page.locator(".receipt-code strong")).toHaveText(code);

  // The receiver completes the handoff with the original six digits.
  await page.getByRole("button", { name: "短码接收" }).click();
  await page.getByLabel("接收码").fill(code);
  await page.getByLabel("接收人").fill("night-bob");
  await page.getByRole("button", { name: "确认接收" }).click();
  await expect(page.getByRole("alert")).toContainText("已到达交接窗");

  // Read the adjudication straight back from the real API/PostgreSQL: the
  // container only ever moved to the rerouted target, and the timeline holds
  // exactly one reroute and one receipt event.
  const detailResponse = await request.get(`${API_URL}/batches/${batch.id}`);
  expect(detailResponse.ok()).toBeTruthy();
  const detail = await detailResponse.json();
  expect(detail.containers[0].current_location.code).toBe("WINDOW");
  const rerouted = detail.timeline.filter(
    (event: { event_type: string }) => event.event_type === "handoff_rerouted",
  );
  const received = detail.timeline.filter(
    (event: { event_type: string }) => event.event_type === "handoff_received",
  );
  expect(rerouted).toHaveLength(1);
  expect(received).toHaveLength(1);
  expect(rerouted[0].actor).toBe("alice");
  expect(rerouted[0].details.previous_to).toBe("BENCH");
  expect(rerouted[0].details.to).toBe("WINDOW");
  expect(rerouted[0].details.reason).toBe("处理台临时停用");
  expect(received[0].details.to).toBe("WINDOW");

  const handoffs = await (await request.get(`${API_URL}/handoffs`)).json();
  const stored = handoffs.find((item: { batch_id: string }) => item.batch_id === batch.id);
  expect(stored.status).toBe("received");
  expect(stored.to_location.code).toBe("WINDOW");
  expect(stored.original_to_location.code).toBe("BENCH");
  expect(stored.rerouted_by).toBe("alice");
  expect(stored.reroute_reason).toBe("处理台临时停用");
  expect(stored.rerouted_at).toBeTruthy();
});
