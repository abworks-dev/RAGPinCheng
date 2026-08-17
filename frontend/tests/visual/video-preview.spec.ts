import { expect, test } from "@playwright/test";
import { installChatRoutes } from "./fixtures/admin-fixtures";
import { expectNoBodyOverflow } from "./helpers/layout";

test.describe("视频移动端预览", () => {
  test("视频使用受视口约束的底部弹层且转录可滚动", async ({ page }) => {
    await installChatRoutes(page, "video");
    await page.goto("/");
    await page.getByPlaceholder("向企业知识库提问").fill("打开培训视频");
    await page.getByRole("button", { name: "发送问题" }).click();
    await page.getByRole("button", { name: "查看 1 个来源" }).click();
    await page.getByRole("button", { name: "从 00:00:12 播放" }).click();

    const dialog = page.getByRole("dialog", { name: /项目交付培训视频/ });
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAttribute("data-mobile-presentation", "bottom-sheet");
    await page.waitForTimeout(350);
    await expect(page.getByRole("button", { name: "关闭预览" })).toBeVisible();
    await expect(page.getByLabel("视频播放器")).toBeVisible();
    const transcript = page.getByRole("region", { name: "视频转录稿" });
    await expect(transcript).toBeVisible();

    const viewport = page.viewportSize()!;
    const bounds = await dialog.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(-1);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width + 1);
    expect(bounds!.y).toBeGreaterThanOrEqual(-1);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport.height + 1);
    if (viewport.width < 768) {
      expect(bounds!.height).toBeLessThanOrEqual(viewport.height * 0.93);
      expect(Math.abs(bounds!.y + bounds!.height - viewport.height)).toBeLessThanOrEqual(1);
    }

    const transcriptScroller = transcript.locator("[tabindex='0']");
    await expect.poll(() => transcriptScroller.evaluate((node) => node.scrollHeight > node.clientHeight)).toBe(true);
    await transcriptScroller.evaluate((node) => { node.scrollTop = node.scrollHeight; });
    await expectNoBodyOverflow(page);
  });
});
