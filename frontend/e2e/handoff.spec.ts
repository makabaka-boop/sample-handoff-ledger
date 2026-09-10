import { expect, test } from "@playwright/test";

test("registers a batch through the browser and reads it back from the API", async ({ page, request }) => {
  const suffix = Date.now().toString();
  await page.goto("/");
  await page.getByRole("button", { name: "批次档案" }).click();
  await page.getByRole("button", { name: /登记批次/ }).click();
  await page.getByLabel("批次号").fill(`E2E-${suffix}`);
  await page.getByLabel("温区下限 (°C)").fill("2");
  await page.getByLabel("温区上限 (°C)").fill("8");
  await page.getByLabel("容器标签").fill("E2E-TUBE");
  await page.getByLabel("登记人").fill("e2e-operator");
  await page.getByRole("button", { name: "保存批次" }).click();
  await expect(page.getByText(`E2E-${suffix}`)).toBeVisible();

  const response = await request.get("http://localhost:8000/api/batches");
  expect(response.ok()).toBeTruthy();
  const batches = await response.json();
  expect(batches.some((batch: { accession_number: string }) => batch.accession_number === `E2E-${suffix}`)).toBeTruthy();
});
