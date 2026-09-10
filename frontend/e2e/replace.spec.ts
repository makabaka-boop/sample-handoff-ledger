import { expect, test } from "@playwright/test";

const API_URL = process.env.E2E_API_URL ?? "http://localhost:8000/api";

async function createBatchWithContainer(request: import("@playwright/test").APIRequestContext, suffix: string) {
  const batchResponse = await request.post(`${API_URL}/batches`, {
    data: {
      accession_number: `REPLACE-${suffix}`,
      temperature_zone: "2–8°C",
      max_out_minutes: 30,
      created_by: "alice",
      containers: [{ label: "CRACKED-A", initial_location_code: "FRIDGE" }],
    },
  });
  expect(batchResponse.ok()).toBeTruthy();
  return batchResponse.json();
}

test("transloading seals the old container, keeps one timeline event and hands off from the new one", async ({
  page,
  request,
}) => {
  const suffix = Date.now().toString();
  const batch = await createBatchWithContainer(request, suffix);
  const oldContainer = batch.containers[0];

  await page.goto("/");
  await page.getByRole("button", { name: "批次档案" }).click();
  await page.getByRole("button", { name: `REPLACE-${suffix}` }).click();

  // Only active containers expose the transloading action.
  await page.getByRole("button", { name: "转装替换" }).click();
  await page.getByLabel("新容器标签").fill("SOUND-B");
  await page.getByLabel("操作人").fill("carol");
  await page.getByLabel("替换原因").fill("盘点发现容器破裂");
  await page.getByLabel("备注").fill("样本已转入新容器");
  await page.getByRole("button", { name: "封存原容器并转装" }).click();
  await expect(page.getByText(/继承离柜计时/)).toBeVisible();

  // The old container is sealed and read-only; the successor is active in place.
  await expect(page.getByText("已封存")).toBeVisible();
  await expect(page.getByText("可流转")).toBeVisible();
  await expect(page.getByRole("button", { name: "转装替换" })).toHaveCount(1);

  // Read the adjudicated state straight back from the real API/PostgreSQL.
  const detailResponse = await request.get(`${API_URL}/batches/${batch.id}`);
  expect(detailResponse.ok()).toBeTruthy();
  const detail = await detailResponse.json();
  const old = detail.containers.find((c) => c.id === oldContainer.id);
  const successor = detail.containers.find((c) => c.label === "SOUND-B");
  expect(old.status).toBe("replaced");
  expect(old.replacement_container_id).toBe(successor.id);
  expect(old.replaced_by).toBe("carol");
  expect(old.replacement_reason).toBe("盘点发现容器破裂");
  expect(successor.status).toBe("active");
  expect(successor.current_location.code).toBe("FRIDGE");
  expect(successor.accumulated_out_seconds).toBe(0);

  const replaced = detail.timeline.filter(
    (event: { event_type: string }) => event.event_type === "container_replaced",
  );
  expect(replaced).toHaveLength(1);
  expect(replaced[0].container_id).toBe(oldContainer.id);
  expect(replaced[0].note).toBe("样本已转入新容器");

  // A pending handoff on the sealed container is rejected; the successor can circulate.
  const blocked = await request.post(`${API_URL}/handoffs`, {
    data: {
      container_id: oldContainer.id,
      from_location_code: "FRIDGE",
      to_location_code: "BENCH",
      created_by: "alice",
      ttl_minutes: 10,
    },
  });
  expect(blocked.status()).toBe(409);
  expect((await blocked.json()).error.code).toBe("CONTAINER_ALREADY_REPLACED");

  const handoffResponse = await request.post(`${API_URL}/handoffs`, {
    data: {
      container_id: successor.id,
      from_location_code: "FRIDGE",
      to_location_code: "BENCH",
      created_by: "alice",
      ttl_minutes: 10,
    },
  });
  expect(handoffResponse.ok()).toBeTruthy();
  const handoff = await handoffResponse.json();
  expect(handoff.container_id).toBe(successor.id);
  expect(handoff.container_label).toBe("SOUND-B");
});
