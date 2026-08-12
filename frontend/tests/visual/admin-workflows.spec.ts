import { expect, test } from "@playwright/test";
import { installAdminRoutes, type AdminScenario } from "./fixtures/admin-fixtures";
import { expectInViewport, expectNoBodyOverflow, expectTouchTarget } from "./helpers/layout";

async function openTab(page: Parameters<typeof installAdminRoutes>[0], label: string, scenario: AdminScenario = "normal") {
  await installAdminRoutes(page, scenario);
  await page.goto("/admin");
  if (page.viewportSize()!.width < 1024) {
    const mobileNavigation = page.getByRole("button", { name: "展开管理功能" });
    await expect(mobileNavigation).toBeVisible();
    await mobileNavigation.click();
  }
  await page.getByRole("button", { name: label, exact: true }).click();
}

test.describe("资料工作流", () => {
  test("normal layout keeps navigation and upload controls discoverable", async ({ page }) => {
    await openTab(page, "资料工作流");
    await expect(page.getByRole("heading", { name: "资料工作流" })).toBeVisible();
    await expect(page.locator("p:visible", { hasText: "建筑信息模型交付标准" }).first()).toBeVisible();
    await expectNoBodyOverflow(page);
    await expectInViewport(page.getByRole("button", { name: "刷新" }));
    await expectInViewport(page.getByRole("button", { name: "上传" }));
    if (page.viewportSize()!.width === 768) {
      await expect(page.getByRole("table")).toBeHidden();
      await expect(page.getByRole("button", { name: "确认" })).toBeVisible();
      await expect(page.getByRole("button", { name: "退回" })).toBeVisible();
      await expect(page.getByRole("button", { name: "发布" }).first()).toBeVisible();
    }
    if (page.viewportSize()!.width === 390) {
      await expectTouchTarget(page.getByRole("button", { name: "上传" }));
      await expectInViewport(page.getByRole("button", { name: "提交" }).first());
    }
  });

  for (const scenario of ["loading", "empty", "error", "disabled"] as const) {
    test(`${scenario} state is explicit and contained`, async ({ page }) => {
      await openTab(page, "资料工作流", scenario);
      await expectNoBodyOverflow(page);
      if (scenario === "loading") await expect(page.getByRole("heading", { name: "资料工作流" })).toBeVisible();
      if (scenario === "empty") await expect(page.getByText("暂无资料")).toBeVisible();
      if (scenario === "error") await expect(page.getByText("合成加载失败")).toBeVisible();
      if (scenario === "disabled") {
        await expect(page.getByText("受管资料库当前未启用")).toBeVisible();
        await expect(page.getByRole("button", { name: "上传" })).toBeDisabled();
      }
    });
  }

  test("upload exposes a stable busy state", async ({ page }) => {
    await openTab(page, "资料工作流");
    await page.getByLabel("选择资料文件").setInputFiles({ name: "synthetic.pdf", mimeType: "application/pdf", buffer: Buffer.from("synthetic fixture") });
    const upload = page.getByRole("button", { name: "上传" });
    await upload.click();
    await expect(upload).toBeDisabled();
    await expectNoBodyOverflow(page);
  });
});

test.describe("分类设置", () => {
  test("normal layout keeps form and category actions discoverable", async ({ page }) => {
    await openTab(page, "分类设置");
    await expect(page.getByRole("heading", { name: "分类设置" })).toBeVisible();
    await expect(page.getByText("资料权限")).toHaveCount(0);
    await expectNoBodyOverflow(page);
    const createButton = page.getByRole("button", { name: "新增" });
    await createButton.scrollIntoViewIfNeeded();
    await expectInViewport(createButton);
    if (page.viewportSize()!.width === 390) {
      await expectTouchTarget(createButton);
      const categoryToggle = page.getByRole("checkbox", { name: "公司标准启用" });
      await categoryToggle.scrollIntoViewIfNeeded();
      await expectInViewport(categoryToggle);
      const save = page.getByRole("button", { name: "保存" }).first();
      await save.scrollIntoViewIfNeeded();
      await expectInViewport(save);
    }
  });

  for (const scenario of ["loading", "empty", "error"] as const) {
    test(`${scenario} state is explicit and contained`, async ({ page }) => {
      await openTab(page, "分类设置", scenario);
      await expectNoBodyOverflow(page);
      if (scenario === "loading") await expect(page.getByRole("button", { name: "刷新" })).toBeDisabled();
      if (scenario === "empty") await expect(page.getByText("暂无分类")).toBeVisible();
      if (scenario === "error") await expect(page.getByText("合成加载失败")).toBeVisible();
    });
  }

  test("create exposes a stable busy state", async ({ page }) => {
    await openTab(page, "分类设置");
    await page.getByLabel("稳定标识", { exact: false }).fill("synthetic_new");
    await page.getByLabel("显示编号", { exact: true }).fill("C");
    await page.getByLabel("分类名称", { exact: true }).fill("合成新增分类");
    const create = page.getByRole("button", { name: "新增分类" });
    await create.click();
    await expect(create).toBeDisabled();
  });
});

test.describe("用户权限", () => {
  test("permissions are visible and editable from the user action menu", async ({ page }) => {
    await openTab(page, "用户");
    await expect(page.getByRole("heading", { name: "用户管理" })).toBeVisible();
    await expect(page.getByText("系统管理员 · 全部权限")).toBeVisible();
    await expect(page.getByText("自定义")).toBeVisible();
    await page.getByRole("button", { name: "管理 合成资料员" }).click();
    await page.getByRole("menuitem", { name: "设置权限" }).click();
    await expect(page.getByRole("dialog", { name: "设置资料权限" })).toBeVisible();
    await expectNoBodyOverflow(page);
    if (page.viewportSize()!.width === 390) {
      await expectInViewport(page.getByRole("button", { name: "保存权限" }));
    }
  });

  test("permission group management explains template semantics", async ({ page }) => {
    await openTab(page, "用户");
    await page.getByRole("button", { name: "权限组管理" }).click();
    await expect(page.getByRole("dialog", { name: "权限组管理" })).toContainText("修改模板不会改变既有用户权限");
    await expect(page.getByRole("dialog", { name: "权限组管理" }).getByRole("button", { name: "普通成员 预设" })).toBeVisible();
    await expectNoBodyOverflow(page);
  });
});
