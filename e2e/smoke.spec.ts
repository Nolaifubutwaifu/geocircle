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
  await page.getByRole("button", { name: "Random puzzle" }).click();
  await expect(page).toHaveURL(/\/play\/(?!2$)\d+$/);
  await expect(page.getByRole("button", { name: /Submit (circle|triangle)/ })).toBeVisible();
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

test("halve a country: Austria", async ({ page }) => {
  await page.goto("/country/aut");
  await mapReady(page);
  const box = (await page.locator(".maplibregl-canvas").boundingBox())!;
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  await page.mouse.click(cx, cy);
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx + 60, cy, { steps: 6 });
  await page.mouse.up();
  await page.getByRole("button", { name: "Submit circle" }).click();
  await expect(page.getByText(/of Austria's people\. The target was 50%/)).toBeVisible({ timeout: 20_000 });
});

test("cows vs people: net score", async ({ page }) => {
  await page.goto("/play/13");
  await mapReady(page);
  const box = (await page.locator(".maplibregl-canvas").boundingBox())!;
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await page.getByRole("button", { name: "Submit circle" }).click();
  await expect(page.getByText(/Your circle holds .* cattle and .* people/)).toBeVisible({ timeout: 20_000 });
});

test("countries page lists countries", async ({ page }) => {
  await page.goto("/countries");
  await expect(page.getByRole("heading", { name: "Halve a country" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Austria/ })).toBeVisible();
});

test("archive lists puzzles", async ({ page }) => {
  await page.goto("/archive");
  await expect(page.getByRole("heading", { name: "All puzzles" })).toBeVisible();
  await expect(page.getByRole("link", { name: /^No\. 2 / })).toBeVisible();
});
