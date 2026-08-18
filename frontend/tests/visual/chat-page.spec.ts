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

    const preview = page.getByRole("dialog", { name: "来源 1 预览" });
    await expect(preview).toBeVisible();
    await page.waitForTimeout(2_000);
    await expect(preview).toBeVisible();
    await expectInViewport(preview);
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

    const preview = page.getByRole("dialog", { name: "来源 1 预览" });
    await expect(preview).toBeVisible();
    const [previewBox, scrollerBox] = await Promise.all([
      preview.boundingBox(),
      scroller.boundingBox(),
    ]);
    expect(previewBox).not.toBeNull();
    expect(scrollerBox).not.toBeNull();
    expect(previewBox!.y).toBeGreaterThanOrEqual(scrollerBox!.y + 40);
    await expectInViewport(preview);
    await expectNoBodyOverflow(page);
  });

  test("视频引用悬浮卡提供独立的时间点播放按钮", async ({ page }) => {
    await installChatRoutes(page, "video");
    await page.goto("/");
    await page.getByPlaceholder("向企业知识库提问").fill("合成视频引用问题");
    await page.getByRole("button", { name: "发送问题" }).click();

    await page.getByRole("superscript").hover();
    const preview = page.getByRole("dialog", { name: "来源 1 预览" });
    const title = preview.getByText("项目交付培训视频：移动端长标题适配验证", { exact: true });
    const playButton = preview.getByRole("button", { name: "从 00:00:12 播放视频" });
    const [titleBox, playButtonBox] = await Promise.all([title.boundingBox(), playButton.boundingBox()]);
    expect(titleBox).not.toBeNull();
    expect(playButtonBox).not.toBeNull();
    expect(titleBox!.x + titleBox!.width).toBeLessThanOrEqual(playButtonBox!.x);
    expect(Math.abs(
      titleBox!.y + titleBox!.height / 2 - (playButtonBox!.y + playButtonBox!.height / 2),
    )).toBeLessThanOrEqual(1);
    await playButton.click();

    const player = page.getByRole("dialog").filter({ hasText: "从 00:12 开始播放" });
    await expect(player).toBeVisible();
    await expect(player.getByRole("heading", { name: "项目交付培训视频：移动端长标题适配验证" })).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("可预览文档的引用悬浮卡提供独立预览按钮", async ({ page }) => {
    await installChatRoutes(page, "document");
    await page.goto("/");
    await page.getByPlaceholder("向企业知识库提问").fill("合成文档引用问题");
    await page.getByRole("button", { name: "发送问题" }).click();

    await page.getByRole("superscript").hover();
    await page.getByRole("button", { name: "预览文档：项目交付检查清单" }).click();

    await expect(page.getByRole("heading", { name: "项目交付检查清单" })).toBeVisible();
    await expect(page.getByText("Word 文档", { exact: true })).toBeVisible();
    await expectNoBodyOverflow(page);
  });
});
