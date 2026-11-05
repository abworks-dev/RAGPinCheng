import { expect, test } from "@playwright/test";
import { installAdminRoutes } from "./fixtures/admin-fixtures";
import { expectNoBodyOverflow } from "./helpers/layout";

for (const theme of ["light", "dark"] as const) {
  for (const view of ["content", "content?view=index", "content?view=transcription"]) {
    test(`summary padding alignment and keyboard filters ${theme}: ${view}`, async ({ page }, testInfo) => {
      await page.addInitScript((value) => localStorage.setItem("pincheng-theme", value), theme);
      await installAdminRoutes(page, "normal");
      await page.goto(`/admin/${view}`);
      const cards = page.locator(".admin-metrics .admin-summary");
      await expect(cards.first()).toBeVisible();
      expect(await cards.count()).toBeGreaterThanOrEqual(4);
      for (const card of await cards.all()) {
        const content = card.locator("button > span").first();
        for (const side of ["top", "right", "bottom", "left"]) {
          await expect(content).toHaveCSS(`padding-${side}`, "16px");
        }
        const labels = content.locator(":scope > span").nth(1).locator(":scope > span");
        const title = (await labels.nth(0).boundingBox())!;
        const value = (await labels.nth(1).boundingBox())!;
        const box = (await card.boundingBox())!;
        const icon = (await content.locator("svg").boundingBox())!;
        expect(Math.abs(title.x - value.x)).toBeLessThanOrEqual(1);
        expect(title.y - box.y).toBeGreaterThanOrEqual(15);
        expect(box.y + box.height - value.y - value.height).toBeGreaterThanOrEqual(15);
        expect(icon.x - box.x).toBeGreaterThanOrEqual(15);
        expect(title.x - icon.x - icon.width).toBeLessThanOrEqual(13);
        expect(value.x + value.width).toBeLessThanOrEqual(box.x + box.width);
      }
      await expectNoBodyOverflow(page);
      const filter = cards.nth(1).getByRole("button");
      await filter.focus();
      await expect(filter).toBeFocused();
      await expect(filter).toHaveCSS("--tw-ring-inset", "inset");
      await filter.press("Space");
      await expect(filter).toHaveAttribute("aria-pressed", "true");
      await expect(cards.first().getByRole("button")).toHaveAttribute("aria-pressed", "false");
      const viewport = page.viewportSize()!;
      await page.screenshot({ path: testInfo.outputPath(`summary-${view.replace(/[?=]/g, "-")}-${theme}-focus-${viewport.width}x${viewport.height}.png`), fullPage: true });
      await cards.first().getByRole("button").click();
      await expect(cards.first().getByRole("button")).toHaveAttribute("aria-pressed", "true");
    });
  }
}
