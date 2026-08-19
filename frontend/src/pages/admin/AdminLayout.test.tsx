import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminLayout } from "./AdminLayout";

const mocks = vi.hoisted(() => ({
  user: { real_name: "测试管理员", employee_id: "admin-test", role: "admin", content_permissions: [] as string[] },
  logout: vi.fn(),
  refreshUser: vi.fn().mockResolvedValue(null),
}));

vi.mock("../../context/AuthContext", () => ({
  useAuth: () => ({
    state: { status: "authed", user: mocks.user },
    logout: mocks.logout,
    refreshUser: mocks.refreshUser,
  }),
}));

function AdminTestRouter({ initialPath = "overview" }: { initialPath?: string }) {
  return (
    <MemoryRouter initialEntries={[`/admin/${initialPath}`]}>
      <Routes>
        <Route path="/admin" element={<AdminLayout />}>
          <Route path="overview" element={<div>概览页面内容</div>} />
          <Route path="maintenance" element={<div>系统维护页面内容</div>} />
          <Route path="content" element={<div>资料库页面内容</div>} />
          <Route path="categories" element={<div>分类设置页面内容</div>} />
          <Route path="media" element={<div>视频媒体页面内容</div>} />
          <Route path="index" element={<div>索引任务页面内容</div>} />
          <Route path="users" element={<div>用户页面内容</div>} />
          <Route path="conversations" element={<div>对话页面内容</div>} />
          <Route path="feedback" element={<div>反馈页面内容</div>} />
        </Route>
        <Route path="/" element={<div>对话工作台内容</div>} />
      </Routes>
    </MemoryRouter>
  );
}

function renderAdmin(initialPath = "overview") {
  return render(<AdminTestRouter initialPath={initialPath} />);
}

describe("AdminLayout route boundary", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.user = { real_name: "测试管理员", employee_id: "admin-test", role: "admin", content_permissions: [] };
  });

  it("reserves a stable root scrollbar gutter only while mounted", () => {
    const { container, unmount } = renderAdmin();
    expect(document.documentElement).toHaveClass("admin-scrollbar-stable");
    expect(container.querySelector("main")).toHaveClass("lg:[scrollbar-gutter:stable]");
    unmount();
    expect(document.documentElement).not.toHaveClass("admin-scrollbar-stable");
  });

  it("keeps the brand, theme, and admin actions in the sidebar", async () => {
    renderAdmin();
    expect(screen.getByText("品成 BIM 知识库")).toBeInTheDocument();
    expect(screen.getByText("管理工作台")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /主题：/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /测试管理员/ }));
    expect(await screen.findByRole("button", { name: "返回对话" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "退出登录" })).toBeInTheDocument();
  });

  it("collapses and expands like the conversation sidebar", () => {
    const { container } = renderAdmin();
    fireEvent.click(screen.getByRole("button", { name: "收起管理侧栏" }));
    expect(container.querySelector("aside")).toHaveClass("lg:w-16");
    fireEvent.click(screen.getByRole("button", { name: "展开管理侧栏" }));
    expect(container.querySelector("aside")).toHaveClass("lg:w-[17rem]");
  });

  it("exposes every management entry through an explicit mobile menu", () => {
    renderAdmin();
    fireEvent.click(screen.getByRole("button", { name: "展开管理功能" }));
    const navigation = screen.getByRole("navigation", { name: "管理功能" });
    expect(within(navigation).getAllByRole("link")).toHaveLength(9);
    fireEvent.click(within(navigation).getByRole("link", { name: "分类管理" }));
    expect(screen.getByText("分类设置页面内容")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "展开管理功能" })).toBeInTheDocument();
  });

  it("keeps grouped route order and mounts only the selected page", () => {
    renderAdmin("conversations");
    const navigation = screen.getByRole("navigation", { name: "管理功能" });
    expect(within(navigation).getByText("总览")).toBeInTheDocument();
    expect(within(navigation).getByText("内容管理")).toBeInTheDocument();
    expect(within(navigation).getByText("运营管理")).toBeInTheDocument();
    expect(within(navigation).getAllByRole("link").map((link) => link.textContent?.trim())).toEqual([
      "系统概览", "系统维护", "回答策略", "转录配置", "资料管理", "分类管理", "用户管理", "对话记录", "用户反馈",
    ]);
    expect(screen.getByRole("link", { name: "对话记录" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("对话页面内容")).toBeInTheDocument();
    expect(screen.queryByText("用户页面内容")).not.toBeInTheDocument();
  });

  it("navigates between nested routes and preserves direct entries", () => {
    renderAdmin("maintenance");
    expect(screen.getByText("系统维护页面内容")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "资料管理" }));
    expect(screen.getByText("资料库页面内容")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "资料管理" })).toHaveAttribute("aria-current", "page");
  });

  it("redirects a content user to the first permitted route after permission removal", async () => {
    mocks.user = {
      real_name: "测试资料员",
      employee_id: "editor-test",
      role: "user",
      content_permissions: ["workspace.view", "item.view"],
    };
    renderAdmin("categories");
    expect(screen.queryByRole("link", { name: "分类管理" })).not.toBeInTheDocument();
    expect(await screen.findByText("资料库页面内容")).toBeInTheDocument();
  });

  it("keeps index tasks inside content management and preserves the legacy entry", async () => {
    mocks.user = {
      real_name: "测试资料员",
      employee_id: "editor-test",
      role: "user",
      content_permissions: ["workspace.view", "item.view", "category.view"],
    };
    const firstRender = renderAdmin("index");
    expect(screen.queryByRole("link", { name: "索引任务" })).not.toBeInTheDocument();
    expect(await screen.findByText("资料库页面内容")).toBeInTheDocument();
    firstRender.unmount();

    mocks.user = {
      ...mocks.user,
      content_permissions: ["workspace.view", "item.view", "category.view", "index.view"],
    };
    renderAdmin("index");
    expect(screen.queryByRole("link", { name: "索引任务" })).not.toBeInTheDocument();
    expect(await screen.findByText("资料库页面内容")).toBeInTheDocument();
  });

  it("redirects the legacy media route into the managed-content transcription view", async () => {
    renderAdmin("media");
    expect(screen.queryByRole("link", { name: "转录任务" })).not.toBeInTheDocument();
    expect(await screen.findByText("资料库页面内容")).toBeInTheDocument();
  });

  it("returns users without workspace permissions to chat", async () => {
    mocks.user = {
      real_name: "测试成员",
      employee_id: "member-test",
      role: "user",
      content_permissions: [],
    };
    renderAdmin("content");
    expect(await screen.findByText("对话工作台内容")).toBeInTheDocument();
  });
});
