import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminUsersPage } from "./AdminUsersPage";

const mocks = vi.hoisted(() => ({
  adminListUsers: vi.fn(),
  adminPatchUser: vi.fn(),
  adminListUserConversations: vi.fn(),
  adminGetConversation: vi.fn(),
  groups: vi.fn(),
  updatePermissions: vi.fn(),
  createGroup: vi.fn(),
  updateGroup: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  api: {
    adminListUsers: mocks.adminListUsers,
    adminPatchUser: mocks.adminPatchUser,
    adminListUserConversations: mocks.adminListUserConversations,
    adminGetConversation: mocks.adminGetConversation,
    managedContentPermissionGroups: mocks.groups,
    updateManagedContentPermissions: mocks.updatePermissions,
    createManagedContentPermissionGroup: mocks.createGroup,
    updateManagedContentPermissionGroup: mocks.updateGroup,
  },
}));

vi.mock("../../components/ui/toast", () => ({
  toast: { success: mocks.toastSuccess, error: mocks.toastError },
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
    content_permissions: ["organize", "review", "publish", "manage_categories", "import_server"],
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
    content_permissions: ["organize"],
  },
];

describe("AdminUsersPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.adminListUsers.mockResolvedValue({ users });
    mocks.groups.mockResolvedValue([
      { id: "member", group_key: "member", display_name: "普通成员", permissions: [], is_system: true, is_active: true, updated_at: 1 },
      { id: "bim", group_key: "bim_engineer", display_name: "BIM工程师", permissions: ["organize"], is_system: true, is_active: true, updated_at: 1 },
      { id: "owner", group_key: "content_owner", display_name: "资料负责人", permissions: ["review"], is_system: true, is_active: true, updated_at: 1 },
    ]);
    mocks.updatePermissions.mockResolvedValue({});
    mocks.createGroup.mockResolvedValue({});
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
    expect(screen.getByRole("button", { name: "管理 管理员" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "管理 李工" })).toBeInTheDocument();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
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

  it("opens one contextual action menu and closes it with Escape", async () => {
    render(<AdminUsersPage />);
    const actionsButton = await screen.findByRole("button", { name: "管理 李工" });

    fireEvent.click(actionsButton);
    expect(screen.getByRole("menu", { name: "李工的账号操作" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "设为管理员" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "启用账号" })).toHaveClass("text-success");

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(actionsButton).toHaveFocus();
  });

  it("applies a permission template and saves the exact user permissions", async () => {
    mocks.adminListUsers.mockResolvedValue({ users: [users[0], { ...users[1], is_active: true }] });
    render(<AdminUsersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "管理 李工" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "设置权限" }));
    expect(screen.getByRole("dialog", { name: "设置资料权限" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("选择权限组"), { target: { value: "owner" } });
    fireEvent.click(screen.getByRole("button", { name: "保存权限" }));
    await waitFor(() => expect(mocks.updatePermissions).toHaveBeenCalledWith(2, ["review"]));
  });

  it("keeps permission dialogs mutually exclusive and cancel does not save", async () => {
    mocks.adminListUsers.mockResolvedValue({ users: [users[0], { ...users[1], is_active: true }] });
    render(<AdminUsersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "权限组管理" }));
    expect(screen.getByRole("dialog", { name: "权限组管理" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));

    fireEvent.click(screen.getByRole("button", { name: "管理 李工" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "设置权限" }));
    expect(screen.getByRole("dialog", { name: "设置资料权限" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "权限组管理" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("选择权限组"), { target: { value: "owner" } });
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(mocks.updatePermissions).not.toHaveBeenCalled();
  });

  it("prevents duplicate permission saves and remains editable after failure", async () => {
    mocks.adminListUsers.mockResolvedValue({ users: [users[0], { ...users[1], is_active: true }] });
    let rejectSave: (reason: Error) => void = () => undefined;
    mocks.updatePermissions.mockReturnValue(new Promise((_resolve, reject) => { rejectSave = reject; }));
    render(<AdminUsersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "管理 李工" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "设置权限" }));
    fireEvent.change(screen.getByLabelText("选择权限组"), { target: { value: "owner" } });
    const save = screen.getByRole("button", { name: "保存权限" });
    fireEvent.click(save);
    fireEvent.click(save);
    expect(mocks.updatePermissions).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "保存中…" })).toBeDisabled();
    rejectSave(new Error("权限服务失败"));
    await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith("权限服务失败"));
    expect(screen.getByRole("button", { name: "保存权限" })).toBeEnabled();
    expect(screen.getByRole("dialog", { name: "设置资料权限" })).toBeInTheDocument();
  });

  it("shows administrator and inactive-account permission boundaries", async () => {
    render(<AdminUsersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "管理 管理员" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "设置权限" }));
    expect(screen.getByText("管理员默认拥有全部权限，不能单独取消。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存权限" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    fireEvent.click(screen.getByRole("button", { name: "管理 李工" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "设置权限" }));
    expect(screen.getByText("账号已停用，权限保留但暂不能修改。")).toBeInTheDocument();
    expect(screen.getByLabelText("选择权限组")).toBeDisabled();
  });

  it("creates a reusable custom permission template", async () => {
    render(<AdminUsersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "权限组管理" }));
    fireEvent.click(screen.getByRole("button", { name: "新建权限组" }));
    fireEvent.change(screen.getByLabelText("权限组名称"), { target: { value: "项目发布员" } });
    fireEvent.click(screen.getByLabelText("发布"));
    fireEvent.click(screen.getByRole("button", { name: "创建模板" }));
    await waitFor(() => expect(mocks.createGroup).toHaveBeenCalledWith({ display_name: "项目发布员", permissions: ["publish"] }));
  });

  it("copies a preset into an independent editable template and reports conflicts", async () => {
    mocks.createGroup.mockRejectedValueOnce(new Error("权限组名称已存在"));
    render(<AdminUsersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "权限组管理" }));
    fireEvent.click(screen.getByRole("button", { name: "复制" }));
    const name = screen.getByLabelText("权限组名称");
    expect(name).toHaveValue("普通成员副本");
    expect(name).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "创建模板" }));
    await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith("权限组名称已存在"));
    expect(screen.getByRole("button", { name: "创建模板" })).toBeEnabled();
    expect(screen.getByRole("dialog", { name: "权限组管理" })).toBeInTheDocument();
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
    fireEvent.click(await screen.findByRole("button", { name: "管理 管理员" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "停用账号" }));

    await waitFor(() => expect(mocks.adminPatchUser).toHaveBeenCalledWith(1, { is_active: false }));
    await waitFor(() => expect(mocks.adminListUsers).toHaveBeenCalledTimes(2));
  });

  it("does not change a role when the administrator cancels confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<AdminUsersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "管理 管理员" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "降为普通用户" }));

    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(mocks.adminPatchUser).not.toHaveBeenCalled();
  });

  it("keeps the confirmed role patch and refresh behavior", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mocks.adminPatchUser.mockResolvedValue({ ...users[0], role: "user" });

    render(<AdminUsersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "管理 管理员" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "降为普通用户" }));

    await waitFor(() => expect(mocks.adminPatchUser).toHaveBeenCalledWith(1, { role: "user" }));
    await waitFor(() => expect(mocks.adminListUsers).toHaveBeenCalledTimes(2));
  });

  it("keeps the password prompt, minimum length guard, and reset contract", async () => {
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValueOnce("123").mockReturnValueOnce("secure123");
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    mocks.adminPatchUser.mockResolvedValue(users[0]);

    render(<AdminUsersPage />);
    const actionsButton = await screen.findByRole("button", { name: "管理 管理员" });
    fireEvent.click(actionsButton);
    fireEvent.click(screen.getByRole("menuitem", { name: "重置密码" }));

    expect(alertSpy).toHaveBeenCalledWith("密码至少 6 位");
    expect(mocks.adminPatchUser).not.toHaveBeenCalled();

    fireEvent.click(actionsButton);
    fireEvent.click(screen.getByRole("menuitem", { name: "重置密码" }));
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
