import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type React from "react";

vi.mock("../../api/client", () => ({ api: { listExternalMediaEntries: vi.fn().mockResolvedValue({ source_id: "source-1", parent_relative_path: "", entries: [{ id: "folder:培训/第一章", kind: "folder", name: "第一章", relative_path: "培训/第一章" }] }) } }));
vi.mock("../ui/button", () => ({ Button: (props: React.ButtonHTMLAttributes<HTMLButtonElement>) => <button {...props} /> }));
vi.mock("../ui/loading-state", () => ({ LoadingState: () => null }));
vi.mock("../ui/empty-state", () => ({ EmptyState: () => null }));

describe("ExternalMediaSourcesPanel", () => {
  it("shows a remote child folder and keeps it read-only", async () => {
    const { ExternalFolderBrowser } = await import("./ExternalFolderBrowser");
    render(<ExternalFolderBrowser sourceId="source-1" title="共享目录远程目录" />);
    expect(await screen.findByText("第一章")).toBeInTheDocument();
    expect(screen.getByText("远程子文件夹")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("远程只读")).toBeInTheDocument());
  });
});
