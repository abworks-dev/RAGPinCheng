import { expect, type Locator, type Page } from "@playwright/test";

export async function expectNoBodyOverflow(page: Page) {
  await expect.poll(async () => page.evaluate(() =>
    document.body.scrollWidth <= document.documentElement.clientWidth
      && document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  )).toBe(true);
}

export async function expectInViewport(locator: Locator) {
  await expect(locator).toBeVisible();
  const box = await locator.boundingBox();
  const viewport = locator.page().viewportSize();
  expect(box).not.toBeNull();
  expect(viewport).not.toBeNull();
  if (!box || !viewport) return;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
  expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
}

export async function expectTouchTarget(locator: Locator) {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  if (!box) return;
  expect(box.width, "touch target width").toBeGreaterThanOrEqual(40);
  expect(box.height, "touch target height").toBeGreaterThanOrEqual(40);
}
