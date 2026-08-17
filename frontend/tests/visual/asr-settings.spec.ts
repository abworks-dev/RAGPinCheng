import { expect, test } from "@playwright/test";
import { installAdminRoutes } from "./fixtures/admin-fixtures";
import { expectInViewport, expectNoBodyOverflow } from "./helpers/layout";

test.describe("转录配置", () => {
  test("比较分段预设并展示固定运行身份", async ({ page }, testInfo) => {
    await installAdminRoutes(page);
    await page.goto("/admin/asr");

    await expect(page.getByRole("heading", { name: "转录配置" })).toBeVisible();
    await expect(page.getByRole("group", { name: "时间分段预设" }).getByRole("button")).toHaveCount(3);
    await expect(page.getByText("BIM-2026-0805")).toBeVisible();
    await expect(page.getByText("最长字符")).toBeVisible();
    await expect(page.getByText(/换行、句末标点、逗号和空格/)).toBeVisible();
    await expect(page.getByRole("button", { name: "申请发布" })).toBeEnabled();
    await expectInViewport(page.getByRole("button", { name: "申请发布" }));
    await expectNoBodyOverflow(page);

    const viewport = page.viewportSize()!;
    await page.screenshot({
      path: testInfo.outputPath(`asr-settings-normal-${viewport.width}x${viewport.height}.png`),
      fullPage: true,
    });
  });

  test("覆盖空、错误和禁用状态", async ({ page }, testInfo) => {
    await installAdminRoutes(page, "empty");
    await page.goto("/admin/asr");
    await expect(page.getByText("暂无已资格转录配置")).toBeVisible();
    await expectNoBodyOverflow(page);

    await page.unrouteAll({ behavior: "ignoreErrors" });
    await installAdminRoutes(page, "error");
    await page.goto("/admin/asr");
    await expect(page.getByText("合成加载失败")).toBeVisible();

    await page.unrouteAll({ behavior: "ignoreErrors" });
    await installAdminRoutes(page, "disabled");
    await page.goto("/admin/asr");
    await expect(page.getByText("服务未启用").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "申请发布" })).toBeDisabled();
    await expectNoBodyOverflow(page);
    const viewport = page.viewportSize()!;
    await page.screenshot({
      path: testInfo.outputPath(`asr-settings-disabled-${viewport.width}x${viewport.height}.png`),
      fullPage: true,
    });
  });

  test("发布申请显示忙碌和成功反馈", async ({ page }) => {
    await installAdminRoutes(page);
    await page.goto("/admin/asr");
    await page.getByRole("button", { name: "申请发布" }).click();
    const dialog = page.getByRole("dialog", { name: "申请发布转录配置" });
    await dialog.getByPlaceholder("例如：培训视频需要更密集的时间定位").fill("合成发布原因");
    await dialog.getByRole("button", { name: "确认申请" }).click();
    await expect(dialog.getByRole("button", { name: "提交中…" })).toBeDisabled();
    await expect(page.getByText("待发布处理")).toBeVisible();
    await expect(page.getByText(/合成发布原因/)).toBeVisible();
    await expectNoBodyOverflow(page);
  });
});
