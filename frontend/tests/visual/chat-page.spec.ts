import { expect, test } from "@playwright/test";
import { installChatRoutes } from "./fixtures/admin-fixtures";
import { expectInViewport, expectNoBodyOverflow } from "./helpers/layout";

test.describe("聊天工作台", () => {
  test("可以提交合成问题并看到 SSE 完成态", async ({ page }) => {
    await installChatRoutes(page);
    await page.goto("/");
    const composer = page.getByPlaceholder("向企业知识库提问");
    await expect(composer).toBeVisible();
    await composer.fill("合成问题");
    await page.getByRole("button", { name: "发送问题" }).click();
    await expect(page.getByText("合成回答", { exact: true })).toBeVisible();
    await expect(composer).toBeEnabled();
    await expect(page.getByRole("button", { name: "发送问题" })).toBeDisabled();
    await expectNoBodyOverflow(page);
  });

  test("SSE 错误保留在当前对话中", async ({ page }) => {
    await installChatRoutes(page, "error");
    await page.goto("/");
    await page.getByPlaceholder("向企业知识库提问").fill("合成失败问题");
    await page.getByRole("button", { name: "发送问题" }).click();
    await expect(page.getByText("合成回答失败", { exact: true })).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("引用角标首次悬停后保持来源预览稳定", async ({ page }) => {
    await installChatRoutes(page, "video");
    await page.goto("/");
    await page.getByPlaceholder("向企业知识库提问").fill("合成引用问题");
    await page.getByRole("button", { name: "发送问题" }).click();

    const marker = page.getByRole("superscript");
    await expect(marker).toBeVisible();
    await marker.hover();

    const tooltip = page.getByRole("tooltip");
    await expect(tooltip).toBeVisible();
    await page.waitForTimeout(2_000);
    await expect(tooltip).toBeVisible();
    await expectInViewport(tooltip);
    await expectNoBodyOverflow(page);
  });

  test("顶部消息的引用预览避开渐隐区域", async ({ page }) => {
    await installChatRoutes(page, "video");
    await page.goto("/");
    await page.getByPlaceholder("向企业知识库提问").fill("合成顶部引用问题");
    await page.getByRole("button", { name: "发送问题" }).click();

    const marker = page.getByRole("superscript");
    const scroller = page.locator("[data-message-scroll-container]");
    await marker.evaluate((element) => element.scrollIntoView({ block: "start" }));
    await marker.hover();

    const tooltip = page.getByRole("tooltip");
    await expect(tooltip).toBeVisible();
    const [tooltipBox, scrollerBox] = await Promise.all([
      tooltip.boundingBox(),
      scroller.boundingBox(),
    ]);
    expect(tooltipBox).not.toBeNull();
    expect(scrollerBox).not.toBeNull();
    expect(tooltipBox!.y).toBeGreaterThanOrEqual(scrollerBox!.y + 40);
    await expectInViewport(tooltip);
    await expectNoBodyOverflow(page);
  });
});
