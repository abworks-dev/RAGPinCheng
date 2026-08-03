import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminFeedbackPage } from "./AdminFeedbackPage";

const mocks = vi.hoisted(() => ({
  adminFeedback: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  api: {
    adminFeedback: mocks.adminFeedback,
  },
}));

const entries = [
  {
    ts: "2026-08-02T10:00:00Z",
    kind: "answer",
    rating: "up",
    query: "交付前需要检查哪些内容？",
    note: "回答清晰，可以直接使用。",
    answer_text: "建议依次核对模型、图纸和交付清单。",
    message_id: "message-1",
  },
  {
    ts: "2026-08-02T11:00:00Z",
    kind: "citation",
    rating: "down",
    category: "企业标准",
    doc_title: "项目交付标准",
    section_path: "第三章",
    start_time: "00:12:30",
    parent_id: "parent-1",
  },
];

describe("AdminFeedbackPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.adminFeedback.mockResolvedValue({ entries, total: 8 });
  });

  it("loads and presents feedback with semantic badges and summary counts", async () => {
    render(<AdminFeedbackPage />);

    expect(screen.getByText("正在加载反馈记录…")).toBeInTheDocument();
    expect(await screen.findByText("交付前需要检查哪些内容？")).toBeInTheDocument();
    expect(screen.getByText("回答反馈")).toBeInTheDocument();
    expect(screen.getAllByText("来源反馈")).toHaveLength(2);
    expect(screen.getByText("有帮助", { selector: "div" })).toHaveClass("bg-success/15");
    expect(screen.getByText("需改进", { selector: "div" })).toHaveClass("bg-destructive/15");
    expect(screen.getByText("共 8 条，当前显示最近 2 条。")).toBeInTheDocument();
    expect(mocks.adminFeedback).toHaveBeenCalledWith(200);
  });

  it("keeps the associated answer collapsed until requested", async () => {
    render(<AdminFeedbackPage />);
    await screen.findByText("交付前需要检查哪些内容？");

    const details = screen.getByText("查看关联回答").closest("details");
    expect(details).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("查看关联回答"));
    expect(details).toHaveAttribute("open");
    expect(screen.getByText("建议依次核对模型、图纸和交付清单。")).toBeInTheDocument();
  });

  it("shows a unified empty state", async () => {
    mocks.adminFeedback.mockResolvedValue({ entries: [], total: 0 });

    render(<AdminFeedbackPage />);

    expect(await screen.findByText("暂无反馈记录")).toBeInTheDocument();
    expect(screen.getByText("用户提交回答或来源反馈后，将在这里显示。")).toBeInTheDocument();
  });

  it("shows an inline error and can retry without changing the API contract", async () => {
    mocks.adminFeedback.mockRejectedValueOnce(new Error("反馈服务暂不可用"));

    render(<AdminFeedbackPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("反馈记录加载失败");
    expect(screen.getByRole("alert")).toHaveTextContent("反馈服务暂不可用");

    mocks.adminFeedback.mockResolvedValueOnce({ entries, total: 8 });
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));

    await waitFor(() => expect(mocks.adminFeedback).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("交付前需要检查哪些内容？")).toBeInTheDocument();
  });
});
