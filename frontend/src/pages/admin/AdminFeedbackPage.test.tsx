import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminFeedbackPage } from "./AdminFeedbackPage";

const mocks = vi.hoisted(() => ({
  adminFeedback: vi.fn(),
  adminPatchFeedback: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  api: {
    adminFeedback: mocks.adminFeedback,
    adminPatchFeedback: mocks.adminPatchFeedback,
  },
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
  },
  {
    feedback_id: "feedback-2",
    ts: "2026-08-02T11:00:00Z",
    kind: "citation",
    rating: "down",
    status: "pending",
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

describe("AdminFeedbackPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.adminFeedback.mockResolvedValue(response);
    mocks.adminPatchFeedback.mockResolvedValue({ ...entries[0], status: "in_progress" });
  });

  it("loads the pending queue with workflow counts and filters", async () => {
    render(<AdminFeedbackPage />);
    expect(screen.getByText("正在加载反馈记录…")).toBeInTheDocument();
    expect(await screen.findByText("交付前需要检查哪些内容？")).toBeInTheDocument();
    expect(screen.getAllByText("待处理").length).toBeGreaterThan(1);
    expect(screen.getByText("筛选结果 2 条，第 1/1 页。")).toBeInTheDocument();
    expect(mocks.adminFeedback).toHaveBeenCalledWith({
      status: "pending", kind: "", rating: "", q: "", page: 1, page_size: 20,
    });
  });

  it("starts processing and refreshes the queue", async () => {
    render(<AdminFeedbackPage />);
    await screen.findByText("交付前需要检查哪些内容？");
    fireEvent.click(screen.getAllByRole("button", { name: "开始处理" })[0]);
    await waitFor(() => expect(mocks.adminPatchFeedback).toHaveBeenCalledWith(
      "feedback-1", { status: "in_progress" },
    ));
    await waitFor(() => expect(mocks.adminFeedback).toHaveBeenCalledTimes(2));
  });

  it("records a resolution and note when completing feedback", async () => {
    render(<AdminFeedbackPage />);
    await screen.findByText("交付前需要检查哪些内容？");
    fireEvent.click(screen.getAllByRole("button", { name: "标记完成" })[0]);
    fireEvent.change(screen.getByLabelText("处理结果"), { target: { value: "answer_improved" } });
    fireEvent.change(screen.getByLabelText("处理备注"), { target: { value: "已调整提示词" } });
    fireEvent.click(screen.getByRole("button", { name: "确认完成" }));
    await waitFor(() => expect(mocks.adminPatchFeedback).toHaveBeenCalledWith(
      "feedback-1",
      { status: "resolved", resolution: "answer_improved", admin_note: "已调整提示词" },
    ));
  });

  it("shows a completed empty state for an empty pending queue", async () => {
    mocks.adminFeedback.mockResolvedValue({ ...response, entries: [], total: 0 });
    render(<AdminFeedbackPage />);
    expect(await screen.findByText("所有反馈均已处理")).toBeInTheDocument();
  });

  it("shows an inline error and retries", async () => {
    mocks.adminFeedback.mockRejectedValueOnce(new Error("反馈服务暂不可用"));
    render(<AdminFeedbackPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("反馈操作失败");
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    await waitFor(() => expect(mocks.adminFeedback).toHaveBeenCalledTimes(2));
  });
});
