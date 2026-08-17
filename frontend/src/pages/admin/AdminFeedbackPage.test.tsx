import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminFeedbackPage } from "./AdminFeedbackPage";

const mocks = vi.hoisted(() => ({
  adminFeedback: vi.fn(),
  adminPatchFeedback: vi.fn(),
  adminGetConversation: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  api: {
    adminFeedback: mocks.adminFeedback,
    adminPatchFeedback: mocks.adminPatchFeedback,
    adminGetConversation: mocks.adminGetConversation,
  },
}));

vi.mock("../../components/ui/toast", () => ({
  toast: { success: mocks.toastSuccess, error: mocks.toastError },
}));

const entries = [
  {
    feedback_id: "feedback-1",
    ts: "2026-08-02T10:00:00Z",
    kind: "answer",
    rating: "down",
    status: "pending",
    query: "交付前需要检查哪些内容？",
    note: "引用不够准确。",
    answer_text: "建议依次核对模型、图纸和交付清单。",
    conversation_id: "conversation-1",
  },
  {
    feedback_id: "feedback-2",
    ts: "2026-08-02T11:00:00Z",
    kind: "citation",
    rating: "down",
    status: "pending",
    note: "引用章节不匹配。",
    doc_title: "项目交付标准",
    section_path: "第三章",
  },
];

const response = {
  entries,
  total: 2,
  page: 1,
  page_size: 20,
  counts: { pending: 2, in_progress: 0, resolved: 3, archived: 1 },
};

const conversation = {
  id: "conversation-1",
  title: "项目交付检查",
  user_id: 9,
  created_at: 1_700_000_000,
  updated_at: 1_700_000_100,
  turn_index: 1,
  messages: [
    { id: 1, role: "user", content: "完整对话里的问题", created_at: 1_700_000_000, user_versions: null, answer_versions: null, sources_for_ui: null },
    { id: 2, role: "assistant", content: "完整对话里的回答", created_at: 1_700_000_100, user_versions: null, answer_versions: null, sources_for_ui: [] },
  ],
};

function setMobileViewport(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation(() => ({
      matches,
      media: "(max-width: 1279px)",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

describe("AdminFeedbackPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setMobileViewport(false);
    mocks.adminFeedback.mockResolvedValue(response);
    mocks.adminPatchFeedback.mockResolvedValue({ ...entries[0], status: "in_progress" });
    mocks.adminGetConversation.mockResolvedValue(conversation);
  });

  it("loads the pending workbench with summary cards, filters, and selected detail", async () => {
    render(<AdminFeedbackPage />);
    expect(screen.getByText("正在加载反馈记录…")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "反馈队列" })).toBeInTheDocument();
    expect(screen.getByLabelText("反馈类型")).toBeInTheDocument();
    expect(screen.getByLabelText("用户评价")).toBeInTheDocument();
    expect(screen.getAllByText("交付前需要检查哪些内容？").length).toBeGreaterThan(1);
    expect(screen.getByRole("button", { name: /交付前需要检查哪些内容/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("待处理 · 共 2 条 · 按提交时间倒序")).toBeInTheDocument();
    expect(mocks.adminFeedback).toHaveBeenCalledWith({
      status: "pending", kind: "", rating: "", q: "", page: 1, page_size: 20,
    });
  });

  it("switches status from the overview and clears search filters", async () => {
    render(<AdminFeedbackPage />);
    await screen.findByRole("heading", { name: "反馈队列" });
    fireEvent.click(screen.getByRole("button", { name: /处理中 0/ }));
    await waitFor(() => expect(mocks.adminFeedback).toHaveBeenLastCalledWith(expect.objectContaining({ status: "in_progress" })));

    fireEvent.change(screen.getByLabelText("搜索"), { target: { value: "交付" } });
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    await waitFor(() => expect(mocks.adminFeedback).toHaveBeenLastCalledWith(expect.objectContaining({ q: "交付" })));
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    await waitFor(() => expect(mocks.adminFeedback).toHaveBeenLastCalledWith(expect.objectContaining({ q: "" })));
  });

  it("starts processing, keeps the action busy, and confirms success", async () => {
    let finishPatch: ((value: unknown) => void) | undefined;
    mocks.adminPatchFeedback.mockImplementationOnce(() => new Promise((resolve) => { finishPatch = resolve; }));
    render(<AdminFeedbackPage />);
    await screen.findByRole("button", { name: "开始处理" });
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }));
    expect(screen.getByRole("button", { name: "处理中…" })).toBeDisabled();
    finishPatch?.({ ...entries[0], status: "in_progress" });
    await waitFor(() => expect(mocks.adminPatchFeedback).toHaveBeenCalledWith(
      "feedback-1", { status: "in_progress" },
    ));
    await waitFor(() => expect(mocks.toastSuccess).toHaveBeenCalledWith("已领取反馈"));
    await waitFor(() => expect(mocks.adminFeedback).toHaveBeenCalledTimes(2));
  });

  it("records a resolution and note in the completion dialog", async () => {
    render(<AdminFeedbackPage />);
    await screen.findByRole("button", { name: "标记完成" });
    fireEvent.click(screen.getByRole("button", { name: "标记完成" }));
    const dialog = screen.getByRole("dialog", { name: "完成反馈处理" });
    fireEvent.change(within(dialog).getByLabelText("处理结果"), { target: { value: "answer_improved" } });
    fireEvent.change(within(dialog).getByLabelText("处理备注"), { target: { value: "已调整提示词" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "确认完成" }));
    await waitFor(() => expect(mocks.adminPatchFeedback).toHaveBeenCalledWith(
      "feedback-1",
      { status: "resolved", resolution: "answer_improved", admin_note: "已调整提示词" },
    ));
    await waitFor(() => expect(mocks.toastSuccess).toHaveBeenCalledWith("反馈已完成"));
  });

  it("archives feedback from the detail action", async () => {
    render(<AdminFeedbackPage />);
    await screen.findByRole("button", { name: "归档反馈" });
    fireEvent.click(screen.getByRole("button", { name: "归档反馈" }));
    await waitFor(() => expect(mocks.adminPatchFeedback).toHaveBeenCalledWith(
      "feedback-1", { status: "archived", admin_note: "" },
    ));
  });

  it("loads the complete conversation on demand", async () => {
    render(<AdminFeedbackPage />);
    await screen.findByRole("button", { name: "查看完整对话" });
    fireEvent.click(screen.getByRole("button", { name: "查看完整对话" }));
    const dialog = await screen.findByRole("dialog", { name: "完整对话" });
    expect(await within(dialog).findByText("完整对话里的问题")).toBeInTheDocument();
    expect(mocks.adminGetConversation).toHaveBeenCalledWith("conversation-1");
  });

  it("opens selected feedback in a mobile sheet", async () => {
    setMobileViewport(true);
    render(<AdminFeedbackPage />);
    const citationItem = await screen.findByRole("button", { name: /关于《项目交付标准》的来源反馈/ });
    fireEvent.click(citationItem);
    const dialog = await screen.findByRole("dialog", { name: "反馈详情" });
    expect(within(dialog).getByText("引用章节不匹配。")).toBeInTheDocument();
    expect(within(dialog).getByText("《项目交付标准》")).toBeInTheDocument();
  });

  it("shows a completed empty state for an empty pending queue", async () => {
    mocks.adminFeedback.mockResolvedValue({ ...response, entries: [], total: 0 });
    render(<AdminFeedbackPage />);
    expect(await screen.findByText("所有反馈均已处理")).toBeInTheDocument();
  });

  it("shows a single recoverable error state", async () => {
    mocks.adminFeedback.mockRejectedValueOnce(new Error("反馈服务暂不可用"));
    render(<AdminFeedbackPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("反馈列表加载失败");
    expect(screen.queryByText("所有反馈均已处理")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    await waitFor(() => expect(mocks.adminFeedback).toHaveBeenCalledTimes(2));
  });
});
