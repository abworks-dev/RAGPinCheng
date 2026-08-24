import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { XMindPreview } from "./XMindPreview";

const mocks = vi.hoisted(() => ({
  preview: vi.fn(),
  fit: vi.fn(),
  narrow: vi.fn(),
  enlarge: vi.fn(),
  destroy: vi.fn(),
}));

vi.mock("../api/admin/content", () => ({
  adminContentApi: { xmindPreview: mocks.preview },
}));

vi.mock("simple-mind-map", () => ({
  default: class {
    view = { fit: mocks.fit, narrow: mocks.narrow, enlarge: mocks.enlarge, setScale: vi.fn() };
    resize() {}
    destroy() { mocks.destroy(); }
  },
}));

describe("XMindPreview", () => {
  beforeEach(() => {
    mocks.preview.mockReset();
    mocks.fit.mockReset();
    mocks.narrow.mockReset();
    mocks.enlarge.mockReset();
    mocks.destroy.mockReset();
  });

  it("switches sheets and expands nested topics", async () => {
    mocks.preview.mockResolvedValue({
      version_id: "version-1",
      sheets: [
        { id: "s1", title: "方案", root_topic: { id: "r1", title: "中心", notes: null, children: [{ id: "c1", title: "阶段一", notes: null, children: [{ id: "c2", title: "任务", notes: null, children: [] }] }] } },
        { id: "s2", title: "风险", root_topic: { id: "r2", title: "风险中心", notes: "注意事项", children: [] } },
      ],
    });

    render(<XMindPreview versionId="version-1" zoom={1.2} />);

    expect(await screen.findByTestId("xmind-map-canvas")).toBeInTheDocument();
    await waitFor(() => expect(mocks.fit).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "放大思维导图" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "风险" }));
    expect(screen.getByRole("tabpanel", { name: "风险" })).toBeInTheDocument();
    await waitFor(() => expect(mocks.destroy).toHaveBeenCalled());
  });

  it("offers a retry after the preview request fails", async () => {
    mocks.preview.mockRejectedValueOnce(new Error("文件无法解析")).mockResolvedValueOnce({
      version_id: "version-1",
      sheets: [{ id: "s1", title: "恢复", root_topic: { id: "r1", title: "已恢复", notes: null, children: [] } }],
    });

    render(<XMindPreview versionId="version-1" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("文件无法解析");
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    await waitFor(() => expect(screen.getByTestId("xmind-map-canvas")).toBeInTheDocument());
    expect(mocks.preview).toHaveBeenCalledTimes(2);
  });
});
