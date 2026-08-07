import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ResourcePreviewShell } from "./ResourcePreviewShell";

describe("ResourcePreviewShell animation state", () => {
  it("applies the shared enter state to every resource preview", () => {
    const { rerender } = render(
      <ResourcePreviewShell open={false} title="资料预览" onClose={vi.fn()}>
        内容
      </ResourcePreviewShell>,
    );

    const root = screen.getByText("内容").closest(".resource-preview-root");
    expect(root).toHaveAttribute("aria-hidden", "true");
    expect(root).not.toHaveClass("resource-preview-open");

    rerender(
      <ResourcePreviewShell open title="资料预览" onClose={vi.fn()}>
        内容
      </ResourcePreviewShell>,
    );

    expect(root).toHaveAttribute("aria-hidden", "false");
    expect(root).toHaveClass("resource-preview-open");
    expect(screen.getByRole("dialog", { name: "资料预览" })).toHaveClass("resource-preview-panel");
    expect(screen.getByRole("button", { name: "关闭资源预览" })).toHaveClass("resource-preview-backdrop");
  });
});
