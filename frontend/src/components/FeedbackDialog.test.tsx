import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FeedbackDialog } from "./FeedbackDialog";

const mocks = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
}));

vi.mock("./ui/toast", () => ({
  toast: {
    success: mocks.toastSuccess,
    error: vi.fn(),
  },
}));

describe("FeedbackDialog", () => {
  it("shows the fixed entry category and submits the selected citation issue", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <FeedbackDialog
        category="引用问题"
        description="选择引用问题"
        reasons={["引用内容不符", "来源定位错误", "资料已过时", "其他"]}
        notePlaceholder="补充引用问题"
        successMessage="引用问题已提交"
        onSubmit={onSubmit}
        trigger={<button type="button">报告引用问题</button>}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "报告引用问题" }));
    expect(screen.getByLabelText("问题分类：引用问题")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "来源定位错误" }));
    fireEvent.change(screen.getByLabelText("补充说明（选填）"), {
      target: { value: "页码与原文不一致" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交" }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        reason: "来源定位错误",
        note: "页码与原文不一致",
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(mocks.toastSuccess).toHaveBeenCalledWith("引用问题已提交");
  });
});
