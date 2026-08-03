import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminConversationsPage } from "./AdminConversationsPage";

const mocks = vi.hoisted(() => ({
  adminListAllConversations: vi.fn(),
  adminGetConversation: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  api: {
    adminListAllConversations: mocks.adminListAllConversations,
    adminGetConversation: mocks.adminGetConversation,
  },
}));

const conversations = [
  {
    id: "conversation-1",
    title: "项目交付标准",
    user_id: 11,
    employee_id: "pc001",
    real_name: "张工",
    created_at: 1_720_000_000,
    updated_at: 1_720_003_600,
    turn_index: 3,
  },
  {
    id: "conversation-2",
    title: "机电模型检查",
    user_id: 12,
    employee_id: "pc002",
    real_name: "李工",
    created_at: 1_721_000_000,
    updated_at: 1_721_003_600,
    turn_index: 2,
  },
];

const conversationState = {
  id: "conversation-1",
  title: "项目交付标准",
  user_id: 11,
  created_at: 1_720_000_000,
  updated_at: 1_720_003_600,
  turn_index: 3,
  messages: [
    { id: 1, role: "user" as const, content: "交付前需要检查哪些内容？" },
    { id: 2, role: "assistant" as const, content: "建议依次核对模型、图纸和交付清单。" },
  ],
};

describe("AdminConversationsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads the existing 200-conversation window and renders the list", async () => {
    mocks.adminListAllConversations.mockResolvedValue({ conversations });

    render(<AdminConversationsPage />);

    expect(screen.getByText("正在加载对话…")).toBeInTheDocument();
    expect(await screen.findByText("项目交付标准")).toBeInTheDocument();
    expect(screen.getByText("机电模型检查")).toBeInTheDocument();
    expect(screen.getAllByText("共 2 条")).toHaveLength(1);
    expect(screen.queryByText("2 条对话")).not.toBeInTheDocument();
    expect(mocks.adminListAllConversations).toHaveBeenCalledTimes(1);
    expect(mocks.adminListAllConversations).toHaveBeenCalledWith(200);
  });

  it("filters by real name, employee id, and conversation title", async () => {
    mocks.adminListAllConversations.mockResolvedValue({ conversations });

    render(<AdminConversationsPage />);
    await screen.findByText("项目交付标准");
    const filter = screen.getByRole("searchbox", { name: "筛选对话" });

    fireEvent.change(filter, { target: { value: "李工" } });
    expect(screen.getByText("机电模型检查")).toBeInTheDocument();
    expect(screen.queryByText("项目交付标准")).not.toBeInTheDocument();

    fireEvent.change(filter, { target: { value: "pc001" } });
    expect(screen.getByText("项目交付标准")).toBeInTheDocument();
    expect(screen.queryByText("机电模型检查")).not.toBeInTheDocument();

    fireEvent.change(filter, { target: { value: "机电" } });
    expect(screen.getByText("机电模型检查")).toBeInTheDocument();
    expect(screen.getByText("显示 1 / 2 条")).toBeInTheDocument();
  });

  it("clears a filter and restores the full list", async () => {
    mocks.adminListAllConversations.mockResolvedValue({ conversations });

    render(<AdminConversationsPage />);
    await screen.findByText("项目交付标准");
    fireEvent.change(screen.getByRole("searchbox", { name: "筛选对话" }), { target: { value: "不存在" } });

    expect(screen.getByText("没有匹配的对话")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "清空筛选" })[0]);

    expect(screen.getByText("项目交付标准")).toBeInTheDocument();
    expect(screen.getByText("机电模型检查")).toBeInTheDocument();
  });

  it("loads and displays the selected conversation without changing the API contract", async () => {
    mocks.adminListAllConversations.mockResolvedValue({ conversations });
    mocks.adminGetConversation.mockResolvedValue(conversationState);

    render(<AdminConversationsPage />);
    const selectedButton = await screen.findByRole("button", { name: /项目交付标准/ });
    fireEvent.click(selectedButton);

    expect(await screen.findByText("交付前需要检查哪些内容？")).toBeInTheDocument();
    const assistantMessage = screen.getByText("建议依次核对模型、图纸和交付清单。");
    expect(assistantMessage).toBeInTheDocument();
    expect(assistantMessage.closest("article")).toHaveClass("w-fit", "max-w-3xl");
    expect(assistantMessage.closest("article")).not.toHaveClass("w-full");
    expect(selectedButton).toHaveClass("border-l-primary", "bg-primary/10");
    expect(screen.getByText("助手")).toHaveClass("border-border", "bg-card");
    expect(mocks.adminGetConversation).toHaveBeenCalledTimes(1);
    expect(mocks.adminGetConversation).toHaveBeenCalledWith("conversation-1");
  });

  it("shows a list loading failure without requesting details", async () => {
    mocks.adminListAllConversations.mockRejectedValue(new Error("对话服务暂不可用"));

    render(<AdminConversationsPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("对话列表加载失败");
    expect(screen.getByRole("alert")).toHaveTextContent("对话服务暂不可用");
    expect(mocks.adminGetConversation).not.toHaveBeenCalled();
  });

  it("replaces the native alert with an inline detail error", async () => {
    mocks.adminListAllConversations.mockResolvedValue({ conversations });
    mocks.adminGetConversation.mockRejectedValue(new Error("详情读取失败"));

    render(<AdminConversationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /项目交付标准/ }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("对话详情加载失败"));
    expect(screen.getByRole("alert")).toHaveTextContent("详情读取失败");
  });
});
