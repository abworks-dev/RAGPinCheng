import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminUsersPage } from "./AdminUsersPage";

const mocks = vi.hoisted(() => ({
  adminListUsers: vi.fn(),
  adminPatchUser: vi.fn(),
  adminListUserConversations: vi.fn(),
  adminGetConversation: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  api: {
    adminListUsers: mocks.adminListUsers,
    adminPatchUser: mocks.adminPatchUser,
    adminListUserConversations: mocks.adminListUserConversations,
    adminGetConversation: mocks.adminGetConversation,
  },
}));

const users = [
  {
    id: 1,
    employee_id: "admin",
    real_name: "管理员",
    role: "admin" as const,
    is_active: true,
    created_at: 1_720_000_000,
    last_login_at: 1_721_000_000,
    conversation_count: 3,
  },
  {
    id: 2,
    employee_id: "pc002",
    real_name: "李工",
    role: "user" as const,
    is_active: false,
    created_at: 1_720_100_000,
    last_login_at: null,
    conversation_count: 1,
  },
];

describe("AdminUsersPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.adminListUsers.mockResolvedValue({ users });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads users and presents role and status badges", async () => {
    render(<AdminUsersPage />);

    expect(screen.getByText("正在加载用户…")).toBeInTheDocument();
    expect(await screen.findByText("admin")).toBeInTheDocument();
    expect(screen.getAllByText("管理员")).toHaveLength(2);
    expect(screen.getByText("李工")).toBeInTheDocument();
    expect(screen.getByText("共 2 位用户")).toBeInTheDocument();
    expect(screen.getByText("已停用")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "停用账号" })[0]).toHaveClass("border-destructive/50");
    expect(screen.getAllByRole("button", { name: "设为管理员" })[0]).toHaveClass("border-border");
    expect(screen.getAllByRole("button", { name: "重置密码" })[0]).toHaveClass("bg-secondary");
    expect(mocks.adminListUsers).toHaveBeenCalledTimes(1);
  });

  it("filters by real name and employee id and clears the filter", async () => {
    render(<AdminUsersPage />);
    await screen.findByText("admin");
    const filter = screen.getByRole("searchbox", { name: "筛选用户" });

    fireEvent.change(filter, { target: { value: "李工" } });
    expect(screen.getByText("李工")).toBeInTheDocument();
    expect(screen.queryByText("admin")).not.toBeInTheDocument();

    fireEvent.change(filter, { target: { value: "admin" } });
    expect(screen.getByText("admin")).toBeInTheDocument();
    expect(screen.getByText("显示 1 / 2 位")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "清空筛选" }));
    expect(screen.getByText("李工")).toBeInTheDocument();
  });

  it("surfaces a user-list loading failure", async () => {
    mocks.adminListUsers.mockRejectedValue(new Error("用户服务暂不可用"));

    render(<AdminUsersPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("用户列表加载失败");
    expect(screen.getByRole("alert")).toHaveTextContent("用户服务暂不可用");
  });

  it("keeps the existing active-state patch and refresh behavior", async () => {
    mocks.adminPatchUser.mockResolvedValue(users[0]);

    render(<AdminUsersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "停用账号" }));

    await waitFor(() => expect(mocks.adminPatchUser).toHaveBeenCalledWith(1, { is_active: false }));
    await waitFor(() => expect(mocks.adminListUsers).toHaveBeenCalledTimes(2));
  });

  it("does not change a role when the administrator cancels confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<AdminUsersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "降为用户" }));

    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(mocks.adminPatchUser).not.toHaveBeenCalled();
  });

  it("keeps the confirmed role patch and refresh behavior", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mocks.adminPatchUser.mockResolvedValue({ ...users[0], role: "user" });

    render(<AdminUsersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "降为用户" }));

    await waitFor(() => expect(mocks.adminPatchUser).toHaveBeenCalledWith(1, { role: "user" }));
    await waitFor(() => expect(mocks.adminListUsers).toHaveBeenCalledTimes(2));
  });

  it("keeps the password prompt, minimum length guard, and reset contract", async () => {
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValueOnce("123").mockReturnValueOnce("secure123");
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    mocks.adminPatchUser.mockResolvedValue(users[0]);

    render(<AdminUsersPage />);
    const resetButtons = await screen.findAllByRole("button", { name: "重置密码" });
    fireEvent.click(resetButtons[0]);

    expect(alertSpy).toHaveBeenCalledWith("密码至少 6 位");
    expect(mocks.adminPatchUser).not.toHaveBeenCalled();

    fireEvent.click(resetButtons[0]);
    await waitFor(() => expect(mocks.adminPatchUser).toHaveBeenCalledWith(1, { reset_password: "secure123" }));
    expect(alertSpy).toHaveBeenCalledWith("密码已重置；该用户的所有会话已失效。");
    expect(promptSpy).toHaveBeenCalledTimes(2);
  });

  it("keeps the user conversation drill-in calls and read-only message view", async () => {
    mocks.adminListUserConversations.mockResolvedValue({
      conversations: [
        {
          id: "conversation-1",
          title: "交付标准",
          user_id: 1,
          employee_id: "admin",
          real_name: "管理员",
          created_at: 1_720_000_000,
          updated_at: 1_721_000_000,
          turn_index: 1,
        },
      ],
    });
    mocks.adminGetConversation.mockResolvedValue({
      id: "conversation-1",
      title: "交付标准",
      user_id: 1,
      created_at: 1_720_000_000,
      updated_at: 1_721_000_000,
      turn_index: 1,
      messages: [
        { id: 1, role: "user", content: "交付前检查什么？" },
        { id: 2, role: "assistant", content: "检查模型、图纸和清单。" },
      ],
    });

    render(<AdminUsersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "查看 管理员 的 3 条对话" }));

    expect(await screen.findByRole("dialog", { name: "管理员的对话" })).toBeInTheDocument();
    expect(mocks.adminListUserConversations).toHaveBeenCalledWith(1);
    fireEvent.click(await screen.findByRole("button", { name: /交付标准/ }));

    expect(await screen.findByText("交付前检查什么？")).toBeInTheDocument();
    expect(screen.getByText("检查模型、图纸和清单。")).toBeInTheDocument();
    expect(mocks.adminGetConversation).toHaveBeenCalledWith("conversation-1");
  });
});
