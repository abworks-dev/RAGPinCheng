import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminLayout } from "./AdminLayout";

const mocks = vi.hoisted(() => ({
  user: { real_name: "测试管理员", employee_id: "admin-test", role: "admin", content_permissions: [] as string[] },
  logout: vi.fn(),
  usersMount: vi.fn(),
  conversationsMount: vi.fn(),
  documentsMount: vi.fn(),
  mediaMount: vi.fn(),
  overviewMount: vi.fn(),
  feedbackMount: vi.fn(),
  managedMount: vi.fn(),
  categoriesMount: vi.fn(),
  maintenanceMount: vi.fn(),
  refreshUser: vi.fn().mockResolvedValue(null),
}));

vi.mock("../../context/AuthContext", () => ({
  useAuth: () => ({
    state: {
      status: "authed",
      user: mocks.user,
    },
    logout: mocks.logout,
    refreshUser: mocks.refreshUser,
  }),
}));

vi.mock("./AdminUsersPage", () => ({
  AdminUsersPage: () => {
    mocks.usersMount();
    return <div>用户页面内容</div>;
  },
}));

vi.mock("./AdminConversationsPage", () => ({
  AdminConversationsPage: () => {
    mocks.conversationsMount();
    return <div>对话页面内容</div>;
  },
}));

vi.mock("./AdminDocumentsPage", () => ({
  AdminDocumentsPage: () => {
    mocks.documentsMount();
    return <div>索引任务页面内容</div>;
  },
}));

vi.mock("./AdminMediaPage", () => ({
  AdminMediaPage: () => {
    mocks.mediaMount();
    return <div>视频管理页面内容</div>;
  },
}));

vi.mock("./AdminOverviewPage", () => ({
  AdminOverviewPage: () => {
    mocks.overviewMount();
    return <div>概览页面内容</div>;
  },
}));

vi.mock("./AdminFeedbackPage", () => ({
  AdminFeedbackPage: () => {
    mocks.feedbackMount();
    return <div>反馈页面内容</div>;
  },
}));

vi.mock("./AdminManagedContentPage", () => ({
  AdminManagedContentPage: () => {
    mocks.managedMount();
    return <div>资料管理页面内容</div>;
  },
}));

vi.mock("./AdminCategoriesPage", () => ({
  AdminCategoriesPage: () => {
    mocks.categoriesMount();
    return <div>分类管理页面内容</div>;
  },
}));

vi.mock("./AdminMaintenancePage", () => ({
  AdminMaintenancePage: () => {
    mocks.maintenanceMount();
    return <div>系统维护页面内容</div>;
  },
}));

function LocationProbe() {
  const { search } = useLocation();
  return <output data-testid="location-search">{search}</output>;
}

describe("AdminLayout tab boundary", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.user = { real_name: "测试管理员", employee_id: "admin-test", role: "admin", content_permissions: [] };
  });

  it("reserves a stable root scrollbar gutter only while mounted", () => {
    const { container, unmount } = render(
      <MemoryRouter>
        <AdminLayout />
      </MemoryRouter>,
    );

    expect(document.documentElement).toHaveClass("admin-scrollbar-stable");
    expect(container.querySelector("main")).toHaveClass("lg:[scrollbar-gutter:stable]");
    unmount();
    expect(document.documentElement).not.toHaveClass("admin-scrollbar-stable");
  });

  it("keeps the brand, theme, and admin actions in the sidebar", async () => {
    render(
      <MemoryRouter>
        <AdminLayout />
      </MemoryRouter>,
    );

    expect(screen.getByText("品成 BIM 知识库")).toBeInTheDocument();
    expect(screen.getByText("管理工作台")).toBeInTheDocument();
    expect(screen.queryByText("测试管理员（admin-test）")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /主题：/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /测试管理员/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "退出登录" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /测试管理员/ }));
    expect(await screen.findByRole("button", { name: "返回对话" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "退出登录" })).toBeInTheDocument();
    expect(document.querySelector("header")).not.toBeInTheDocument();
  });

  it("collapses and expands like the conversation sidebar", () => {
    const { container } = render(
      <MemoryRouter>
        <AdminLayout />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "收起管理侧栏" }));
    expect(container.querySelector("aside")).toHaveClass("lg:w-16");
    expect(screen.getByText("品成 BIM 知识库").parentElement?.parentElement?.parentElement).toHaveClass("lg:hidden");
    expect(screen.getByRole("button", { name: "展开管理侧栏" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "展开管理侧栏" }));
    expect(container.querySelector("aside")).toHaveClass("lg:w-[17rem]");
    expect(screen.getByText("品成 BIM 知识库")).toBeInTheDocument();
  });

  it("exposes every management entry through an explicit mobile menu", () => {
    render(<MemoryRouter><AdminLayout /></MemoryRouter>);
    const menu = screen.getByRole("button", { name: "展开管理功能" });
    fireEvent.click(menu);
    expect(screen.getByRole("button", { name: "收起管理功能" })).toBeInTheDocument();
    const navigation = screen.getByRole("navigation", { name: "管理功能" });
    expect(within(navigation).getAllByRole("button")).toHaveLength(9);
    fireEvent.click(within(navigation).getByRole("button", { name: "分类管理" }));
    expect(screen.getByText("分类管理页面内容")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "展开管理功能" })).toBeInTheDocument();
  });

  it("matches the conversation sidebar width and color tokens", () => {
    const { container } = render(
      <MemoryRouter>
        <AdminLayout />
      </MemoryRouter>,
    );

    expect(container.querySelector("aside")).toHaveClass(
      "bg-sidebar",
      "text-sidebar-foreground",
      "lg:w-[17rem]",
    );
  });

  it("keeps the tab order, current marker, and mounts only the selected page", () => {
    render(
      <MemoryRouter>
        <AdminLayout />
      </MemoryRouter>,
    );

    const navigation = screen.getByRole("navigation", { name: "管理功能" });
    expect(within(navigation).getByText("总览")).toBeInTheDocument();
    expect(within(navigation).getByText("内容管理")).toBeInTheDocument();
    expect(within(navigation).getByText("运营管理")).toBeInTheDocument();
    expect(within(navigation).getAllByRole("button").map((button) => button.textContent?.trim())).toEqual([
      "概览",
      "系统维护",
      "资料管理",
      "分类管理",
      "视频管理",
      "索引任务",
      "用户管理",
      "对话记录",
      "用户反馈",
    ]);

    expect(screen.getByRole("button", { name: "概览" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("概览页面内容")).toBeInTheDocument();
    expect(screen.queryByText("用户页面内容")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "对话记录" }));
    expect(screen.getByRole("button", { name: "对话记录" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "用户管理" })).not.toHaveAttribute("aria-current");
    expect(screen.getByText("对话页面内容")).toBeInTheDocument();
    expect(screen.queryByText("用户页面内容")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "资料管理" }));
    expect(screen.getByText("资料管理页面内容")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "索引任务" }));
    expect(screen.getByText("索引任务页面内容")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "分类管理" }));
    expect(screen.getByText("分类管理页面内容")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "视频管理" }));
    expect(screen.getByText("视频管理页面内容")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "概览" }));
    expect(screen.getByText("概览页面内容")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "用户反馈" }));
    expect(screen.getByText("反馈页面内容")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "用户管理" }));
    expect(screen.getByText("用户页面内容")).toBeInTheDocument();
    expect(mocks.usersMount).toHaveBeenCalledTimes(1);
  });

  it("uses the overview on a bare entry and preserves a selected page on reload", () => {
    const firstRender = render(
      <MemoryRouter initialEntries={["/admin"]}>
        <LocationProbe />
        <AdminLayout />
      </MemoryRouter>,
    );

    expect(screen.getByText("概览页面内容")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "资料管理" }));
    expect(screen.getByTestId("location-search")).toHaveTextContent("?tab=managed");
    firstRender.unmount();

    render(
      <MemoryRouter initialEntries={["/admin?tab=managed"]}>
        <AdminLayout />
      </MemoryRouter>,
    );

    expect(screen.getByRole("button", { name: "资料管理" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("资料管理页面内容")).toBeInTheDocument();
    expect(screen.queryByText("概览页面内容")).not.toBeInTheDocument();
  });

  it("returns a content user to the library when category permission is removed", () => {
    mocks.user = {
      real_name: "测试资料员",
      employee_id: "editor-test",
      role: "user",
      content_permissions: ["organize", "manage_categories"],
    };
    const { rerender } = render(<MemoryRouter><AdminLayout /></MemoryRouter>);

    fireEvent.click(screen.getByRole("button", { name: "分类管理" }));
    expect(screen.getByText("分类管理页面内容")).toBeInTheDocument();

    mocks.user = { ...mocks.user, content_permissions: ["organize"] };
    rerender(<MemoryRouter><AdminLayout /></MemoryRouter>);

    expect(screen.queryByRole("button", { name: "分类管理" })).not.toBeInTheDocument();
    expect(screen.getByText("资料管理页面内容")).toBeInTheDocument();
  });
});
