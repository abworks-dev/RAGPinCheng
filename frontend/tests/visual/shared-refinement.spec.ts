import { expect, test } from "@playwright/test";
import { installAdminRoutes, installAuthRoutes } from "./fixtures/admin-fixtures";
import { expectNoBodyOverflow } from "./helpers/layout";

test("admin menus stay in the viewport after desktop collapse and mobile resize", async ({ page }) => {
  await installAdminRoutes(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/admin/users");
  await page.getByRole("button", { name: "收起管理侧栏", exact: true }).click();
  await expect(page.getByRole("button", { name: "展开管理侧栏", exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  for (const navigationOpen of [false, true]) {
    if (navigationOpen) await page.getByRole("button", { name: "展开管理功能", exact: true }).click();
    for (const trigger of [
    page.getByRole("button", { name: "账户：合成管理员", exact: true }),
    page.getByRole("button", { name: /^主题：/ }),
  ]) {
    await trigger.click();
    const popup = trigger.locator("..").locator("div.absolute");
    await expect(popup).toBeVisible();
    await expect.poll(async () => {
      const box = await popup.boundingBox();
      return Boolean(box && box.x >= 0 && box.y >= 0 && box.x + box.width <= 390 && box.y + box.height <= 844);
    }).toBe(true);
    await expectNoBodyOverflow(page);
    await page.keyboard.press("Escape");
    await expect(trigger).toBeFocused();
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
    }
  }
});

for (const theme of ["light", "dark"] as const) {
  test(`shared semantic colors and account keyboard focus in ${theme}`, async ({ page }, testInfo) => {
    await page.addInitScript((value) => localStorage.setItem("pincheng-theme", value), theme);
    await installAdminRoutes(page);
    await page.goto("/admin/users");
    await expect(page.getByRole("heading", { name: "用户管理", exact: true })).toBeVisible();
    await expectNoBodyOverflow(page);
    const contrasts = await page.evaluate(() => {
      const style = getComputedStyle(document.documentElement);
      const token = (name: string) => style.getPropertyValue(name).trim().split(/\s+/).map(Number);
      const luminance = (rgb: number[]) => rgb.map((v) => v / 255).map((v) => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
        .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
      const ratio = (a: number[], b: number[]) => {
        const values = [luminance(a), luminance(b)].sort((x, y) => y - x);
        return (values[0] + 0.05) / (values[1] + 0.05);
      };
      const card = token("--card");
      return ["success", "warning", "info", "destructive"].map((name) => {
        const color = token(`--${name}`);
        const tint = color.map((v, i) => v * 0.15 + card[i] * 0.85);
        return { name, ratio: ratio(color, tint) };
      });
    });
    for (const value of contrasts) expect(value.ratio, `${theme} ${value.name} text on tinted surface`).toBeGreaterThanOrEqual(4.5);
    const account = page.getByRole("button", { name: "账户：合成管理员", exact: true }).filter({ visible: true });
    await account.scrollIntoViewIfNeeded();
    await account.focus();
    await expect(account).toBeFocused();
    await account.press("Enter");
    await expect(page.getByRole("button", { name: "返回对话", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(account).toBeFocused();
    await expect(account).toHaveAttribute("aria-expanded", "false");
    const viewport = page.viewportSize()!;
    await page.screenshot({ path: testInfo.outputPath(`users-${theme}-${viewport.width}x${viewport.height}.png`), fullPage: true });
  });

  test(`auth shared controls retain layout in ${theme}`, async ({ page }, testInfo) => {
    await page.addInitScript((value) => {
      localStorage.setItem("pincheng-theme", value);
      document.documentElement.classList.toggle("dark", value === "dark");
    }, theme);
    await installAuthRoutes(page);
    for (const route of ["login", "register"]) {
      await page.goto(`/${route}`);
      await expect(page.getByRole("button", { name: route === "login" ? "登录" : "注册", exact: true })).toBeVisible();
      await expectNoBodyOverflow(page);
      const viewport = page.viewportSize()!;
      await page.screenshot({ path: testInfo.outputPath(`${route}-${theme}-${viewport.width}x${viewport.height}.png`), fullPage: true });
    }
  });
}
