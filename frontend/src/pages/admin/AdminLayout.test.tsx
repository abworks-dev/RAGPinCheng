import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminLayout } from "./AdminLayout";

const mocks = vi.hoisted(() => ({
  logout: vi.fn(),
  usersMount: vi.fn(),
  conversationsMount: vi.fn(),
  documentsMount: vi.fn(),
  mediaMount: vi.fn(),
  overviewMount: vi.fn(),
  feedbackMount: vi.fn(),
}));

vi.mock("../../context/AuthContext", () => ({
  useAuth: () => ({
    state: {
      status: "authed",
      user: { real_name: "测试管理员", employee_id: "admin-test" },
    },
    logout: mocks.logout,
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
    return <div>资料管理页面内容</div>;
  },
}));

vi.mock("./AdminMediaPage", () => ({
  AdminMediaPage: () => {
    mocks.mediaMount();
    return <div>视频媒体页面内容</div>;
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

describe("AdminLayout tab boundary", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("reserves a stable root scrollbar gutter only while mounted", () => {
    const { unmount } = render(
      <MemoryRouter>
        <AdminLayout />
      </MemoryRouter>,
    );

    expect(document.documentElement).toHaveClass("admin-scrollbar-stable");
    unmount();
    expect(document.documentElement).not.toHaveClass("admin-scrollbar-stable");
  });

  it("keeps the admin identity and return navigation visible", () => {
    render(
      <MemoryRouter>
        <AdminLayout />
      </MemoryRouter>,
    );

    expect(screen.getByText("品成 BIM 知识库")).toBeInTheDocument();
    expect(screen.getByText("测试管理员（admin-test）")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /返回对话/ })).toHaveAttribute("href", "/");
    expect(screen.getByRole("button", { name: "退出" })).toBeInTheDocument();
  });

  it("keeps the tab order, current marker, and mounts only the selected page", () => {
    render(
      <MemoryRouter>
        <AdminLayout />
      </MemoryRouter>,
    );

    const navigation = screen.getByRole("navigation", { name: "管理功能" });
    expect(within(navigation).getAllByRole("button").map((button) => button.textContent?.trim())).toEqual([
      "用户",
      "对话",
      "资料管理",
      "视频媒体",
      "概览",
      "反馈",
    ]);

    expect(screen.getByRole("button", { name: "用户" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("用户页面内容")).toBeInTheDocument();
    expect(screen.queryByText("对话页面内容")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "对话" }));
    expect(screen.getByRole("button", { name: "对话" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "用户" })).not.toHaveAttribute("aria-current");
    expect(screen.getByText("对话页面内容")).toBeInTheDocument();
    expect(screen.queryByText("用户页面内容")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "资料管理" }));
    expect(screen.getByText("资料管理页面内容")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "视频媒体" }));
    expect(screen.getByText("视频媒体页面内容")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "概览" }));
    expect(screen.getByText("概览页面内容")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "反馈" }));
    expect(screen.getByText("反馈页面内容")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "用户" }));
    expect(screen.getByText("用户页面内容")).toBeInTheDocument();
    expect(mocks.usersMount).toHaveBeenCalledTimes(2);
  });
});
