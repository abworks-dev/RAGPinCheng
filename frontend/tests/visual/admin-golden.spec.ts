import { expect, test } from "@playwright/test";
import { installAdminRoutes } from "./fixtures/admin-fixtures";

for (const [tab, slug] of [["资料库", "managed-content"], ["分类设置", "categories"]] as const) {
  test(`${tab} accepted golden`, async ({ page }) => {
    await installAdminRoutes(page, "normal");
    await page.goto("/admin");
    if (page.viewportSize()!.width < 1024) {
      const mobileNavigation = page.getByRole("button", { name: "展开管理功能" });
      await expect(mobileNavigation).toBeVisible();
      await mobileNavigation.click();
    }
    await page.getByRole("button", { name: tab, exact: true }).click();
    await expect(page.getByRole("heading", { name: tab })).toBeVisible();
    const viewport = page.viewportSize()!;
    await expect(page).toHaveScreenshot(`${slug}-normal-${viewport.width}x${viewport.height}.png`, { fullPage: true });
  });
}
