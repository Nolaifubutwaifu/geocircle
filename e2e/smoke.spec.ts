import { expect, test, type Page } from "@playwright/test";

// Waits until the map has loaded and drawn land hexes.
async function mapReady(page: Page) {
  await expect(page.locator("[data-hexes]")).toHaveAttribute("data-hexes", /^[1-9]/, { timeout: 60_000 });
  await page.waitForTimeout(500);
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("geocircle:v1:seen-how-to", "1"));
});

test("circle: drop, drag, submit, reveal", async ({ page }) => {
  await page.goto("/play/2");
  await mapReady(page);
  const box = (await page.locator(".maplibregl-canvas").boundingBox())!;
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  await page.mouse.click(cx, cy);
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx + 40, cy, { steps: 5 });
  await page.mouse.move(cx + 80, cy, { steps: 5 });
  await page.mouse.up();
  await expect(page.locator(".pill")).toHaveText(/km/);
  await page.getByRole("button", { name: "Submit circle" }).click();
  await expect(page.getByText(/You caught \d+(\.\d)?% of the world's rice eaten/)).toBeVisible({ timeout: 20_000 });
});

test("triangle: three taps, submit, reveal", async ({ page }) => {
  await page.goto("/play/3");
  await mapReady(page);
  const box = (await page.locator(".maplibregl-canvas").boundingBox())!;
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  await page.mouse.click(cx - 50, cy - 30);
  await page.mouse.click(cx + 50, cy - 30);
  await page.mouse.click(cx, cy + 50);
  await expect(page.getByText(/km around/)).toBeVisible();
  await page.getByRole("button", { name: "Submit triangle" }).click();
  await expect(page.getByText(/You caught \d+(\.\d)?% of the world's beer drunk/)).toBeVisible({ timeout: 20_000 });
});

test("archive lists puzzles", async ({ page }) => {
  await page.goto("/archive");
  await expect(page.getByRole("heading", { name: "All puzzles" })).toBeVisible();
  await expect(page.getByRole("link", { name: /No\. 2/ })).toBeVisible();
});
