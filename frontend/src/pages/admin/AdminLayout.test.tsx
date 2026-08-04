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

  it("moves theme and admin actions to the bottom of the sidebar", () => {
    const { container } = render(
      <MemoryRouter>
        <AdminLayout />
      </MemoryRouter>,
    );

    expect(screen.getByText("品成 BIM 知识库")).toBeInTheDocument();
    expect(screen.queryByText("测试管理员（admin-test）")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /主题：/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /测试管理员/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "退出登录" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /测试管理员/ }));
    expect(screen.getByRole("button", { name: "返回对话" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "退出登录" })).toBeInTheDocument();
    expect(container.querySelector("header")).not.toHaveClass("border-b");
    expect(container.querySelector("aside")).toHaveClass("lg:sticky", "lg:top-16", "lg:self-start");
    expect(container.querySelector("aside")).not.toHaveClass("border-r");
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
