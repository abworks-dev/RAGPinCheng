import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { XMindPreview } from "./XMindPreview";

const mocks = vi.hoisted(() => ({ preview: vi.fn() }));

vi.mock("../api/admin/content", () => ({
  adminContentApi: { xmindPreview: mocks.preview },
}));

describe("XMindPreview", () => {
  beforeEach(() => mocks.preview.mockReset());

  it("switches sheets and expands nested topics", async () => {
    mocks.preview.mockResolvedValue({
      version_id: "version-1",
      sheets: [
        { id: "s1", title: "方案", root_topic: { id: "r1", title: "中心", notes: null, children: [{ id: "c1", title: "阶段一", notes: null, children: [{ id: "c2", title: "任务", notes: null, children: [] }] }] } },
        { id: "s2", title: "风险", root_topic: { id: "r2", title: "风险中心", notes: "注意事项", children: [] } },
      ],
    });

    render(<XMindPreview versionId="version-1" />);

    expect(await screen.findByText("中心")).toBeInTheDocument();
    expect(screen.getByText("任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "风险" }));
    expect(screen.getByText("风险中心")).toBeInTheDocument();
    expect(screen.getByText("注意事项")).toBeInTheDocument();
  });

  it("offers a retry after the preview request fails", async () => {
    mocks.preview.mockRejectedValueOnce(new Error("文件无法解析")).mockResolvedValueOnce({
      version_id: "version-1",
      sheets: [{ id: "s1", title: "恢复", root_topic: { id: "r1", title: "已恢复", notes: null, children: [] } }],
    });

    render(<XMindPreview versionId="version-1" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("文件无法解析");
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    await waitFor(() => expect(screen.getByText("已恢复")).toBeInTheDocument());
    expect(mocks.preview).toHaveBeenCalledTimes(2);
  });
});
