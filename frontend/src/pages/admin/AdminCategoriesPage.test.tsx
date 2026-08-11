import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminCategoriesPage } from "./AdminCategoriesPage";

const mocks = vi.hoisted(() => ({
  role: "admin",
  categories: vi.fn(),
  permissions: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  updatePermissions: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("../../context/AuthContext", () => ({
  useAuth: () => ({
    state: {
      status: "authed",
      user: { role: mocks.role },
    },
  }),
}));

vi.mock("../../api/client", () => ({
  api: {
    managedCategories: mocks.categories,
    managedContentPermissions: mocks.permissions,
    createManagedCategory: mocks.create,
    updateManagedCategory: mocks.update,
    updateManagedContentPermissions: mocks.updatePermissions,
  },
}));

vi.mock("../../components/ui/toast", () => ({
  toast: { success: mocks.success, error: mocks.error },
}));

const category = {
  id: "cat-01",
  category_key: "industry_standards",
  parent_id: null,
  display_code: "01",
  display_name: "行业规范与标准",
  sort_order: 10,
  level: 1,
  is_active: true,
  version: 3,
  created_at: 1,
  updated_at: 1,
};

describe("AdminCategoriesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.role = "admin";
    mocks.categories.mockResolvedValue([category]);
    mocks.permissions.mockResolvedValue([
      { user_id: 2, employee_id: "u2", real_name: "整理员", role: "user", is_active: true, permissions: ["organize"] },
    ]);
    mocks.update.mockResolvedValue({ ...category, version: 4 });
    mocks.updatePermissions.mockResolvedValue({});
  });

  it("sends the category version for optimistic concurrency", async () => {
    render(<AdminCategoriesPage />);
    const name = await screen.findByLabelText("行业规范与标准名称");
    fireEvent.change(name, { target: { value: "行业规范" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(mocks.update).toHaveBeenCalledWith("cat-01", {
      display_code: "01",
      display_name: "行业规范",
      sort_order: 10,
      is_active: true,
      expected_version: 3,
    }));
  });

  it("updates scoped permissions without changing the global role", async () => {
    render(<AdminCategoriesPage />);
    const checkbox = await screen.findByLabelText("整理员确认");
    fireEvent.click(checkbox);
    await waitFor(() => expect(mocks.updatePermissions).toHaveBeenCalledWith(2, ["organize", "review"]));
  });

  it("lets a category manager edit categories without exposing permission grants", async () => {
    mocks.role = "user";
    render(<AdminCategoriesPage />);
    expect(await screen.findByText("industry_standards")).toBeInTheDocument();
    expect(mocks.permissions).not.toHaveBeenCalled();
    expect(screen.queryByRole("heading", { name: "资料权限" })).not.toBeInTheDocument();
  });
});
