import { expect, test } from "@playwright/test";
import { installAdminRoutes } from "./fixtures/admin-fixtures";
import { expectNoBodyOverflow } from "./helpers/layout";

test.describe("反馈管理", () => {
  test("桌面端反馈队列和详情工作台完整可用", async ({ page }) => {
    test.skip((page.viewportSize()?.width || 0) < 1280, "桌面详情仅在 xl 视口常驻");
    await installAdminRoutes(page);
    await page.goto("/admin/feedback");
    await expect(page.getByRole("heading", { level: 1, name: "用户反馈" })).toBeVisible();
    await expect(page.getByRole("region", { name: "反馈状态概览" })).toBeVisible();
    await expect(page.getByRole("button", { name: "待处理 2", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("button", { name: /项目竣工交付前/ })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("region", { name: "反馈详情" }).getByText("关联问答")).toBeVisible();
    await expect(page.getByRole("button", { name: "开始处理" })).toBeVisible();
    await expect(page.getByRole("button", { name: "标记完成" })).toBeVisible();
    await expect(page.getByRole("button", { name: "归档反馈" })).toBeVisible();
    await page.getByLabel("反馈类型").selectOption("citation");
    await expect(page.getByRole("button", { name: /建筑信息模型交付标准/ })).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("移动端从对象列表打开详情抽屉", async ({ page }) => {
    test.skip((page.viewportSize()?.width || 0) >= 1280, "仅在非 xl 视口验证抽屉交互");
    await installAdminRoutes(page);
    await page.goto("/admin/feedback");
    await page.getByRole("button", { name: /建筑信息模型交付标准/ }).click();
    const detail = page.getByRole("dialog", { name: "反馈详情" });
    await expect(detail).toBeVisible();
    await expect(detail.getByText("关联来源")).toBeVisible();
    await expect(detail.getByText("第三章 / 3.2 文件组织与命名 / 3.2.4 交付目录")).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("完成处理通过确认对话框并显示成功反馈", async ({ page }) => {
    await installAdminRoutes(page, "normal", "admin", undefined, { feedbackPatchDelayMs: 600 });
    await page.goto("/admin/feedback");
    if ((page.viewportSize()?.width || 0) < 1280) {
      await page.getByRole("button", { name: /项目竣工交付前/ }).click();
      await page.getByRole("dialog", { name: "反馈详情" }).getByRole("button", { name: "标记完成" }).click();
    } else {
      await page.getByRole("button", { name: "标记完成" }).click();
    }
    const dialog = page.getByRole("dialog", { name: "完成反馈处理" });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("处理结果").selectOption("answer_improved");
    await dialog.getByLabel("处理备注").fill("已补充交付检查顺序和资料来源。");
    await dialog.getByRole("button", { name: "确认完成" }).click();
    await expect(dialog.getByRole("button", { name: "提交中…" })).toBeDisabled();
    await expect(page.getByText("反馈已完成")).toBeVisible();
    await expect(page.getByText("所有反馈均已处理")).not.toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("反馈加载中保持稳定骨架", async ({ page }) => {
    await installAdminRoutes(page, "loading");
    await page.goto("/admin/feedback");
    await expect(page.getByText("正在加载反馈记录…")).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("待处理队列为空时给出明确下一步", async ({ page }) => {
    await installAdminRoutes(page, "empty");
    await page.goto("/admin/feedback");
    await expect(page.getByText("所有反馈均已处理")).toBeVisible();
    await expect(page.getByRole("button", { name: /待处理 0/ })).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("反馈加载失败保持错误入口", async ({ page }) => {
    await installAdminRoutes(page, "error");
    await page.goto("/admin/feedback");
    await expect(page.getByText("反馈列表加载失败")).toBeVisible();
    await expect(page.getByText("合成加载失败")).toBeVisible();
    await expect(page.getByRole("button", { name: "重新加载" })).toBeVisible();
    await expectNoBodyOverflow(page);
  });
});
