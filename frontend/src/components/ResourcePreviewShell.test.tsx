import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ResourcePreviewShell } from "./ResourcePreviewShell";

describe("ResourcePreviewShell sheet behavior", () => {
  it("renders an optional back action for nested preview contexts", () => {
    const onBack = vi.fn();
    render(
      <ResourcePreviewShell
        open
        title="资料预览"
        onClose={vi.fn()}
        backAction={<button type="button" onClick={onBack}>返回资料详情</button>}
      >
        内容
      </ResourcePreviewShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "返回资料详情" }));
    expect(onBack).toHaveBeenCalledOnce();
  });

  it("renders the preview sheet only while open", () => {
    const { rerender } = render(
      <ResourcePreviewShell open={false} title="资料预览" onClose={vi.fn()}>
        内容
      </ResourcePreviewShell>,
    );

    expect(screen.queryByText("内容")).not.toBeInTheDocument();

    rerender(
      <ResourcePreviewShell open title="资料预览" onClose={vi.fn()}>
        内容
      </ResourcePreviewShell>,
    );

    expect(screen.getByRole("dialog", { name: "资料预览" })).toBeInTheDocument();
    expect(screen.getByText("内容")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "关闭预览" })).toBeInTheDocument();
  });

  it("closes with Escape and restores focus to the opener", async () => {
    const onClose = vi.fn();
    const view = (open: boolean) => (
      <>
        <button type="button">打开预览</button>
        <ResourcePreviewShell open={open} title="资料预览" onClose={onClose}>内容</ResourcePreviewShell>
      </>
    );
    const { rerender } = render(view(false));
    screen.getByRole("button", { name: "打开预览" }).focus();
    rerender(view(true));
    await waitFor(() => expect(screen.getByRole("button", { name: "关闭预览" })).toHaveFocus());

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
    rerender(view(false));
    await waitFor(() => expect(screen.getByRole("button", { name: "打开预览" })).toHaveFocus());
  });
});
