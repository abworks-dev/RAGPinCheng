import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type React from "react";
import { api } from "../../api/client";
import type { ExternalMediaEntryList } from "../../types";

vi.mock("../../api/client", () => ({ api: { listExternalMediaEntries: vi.fn() } }));
vi.mock("../ui/button", () => ({ Button: (props: React.ButtonHTMLAttributes<HTMLButtonElement>) => <button {...props} /> }));
vi.mock("../ui/loading-state", () => ({ LoadingState: () => null }));
vi.mock("../ui/empty-state", () => ({ EmptyState: () => null }));

const entries: ExternalMediaEntryList = {
  source_id: "source-1",
  parent_relative_path: "",
  entries: [
    { id: "folder:培训/第一章", kind: "folder", name: "第一章", relative_path: "培训/第一章" },
    {
      id: "doc-1",
      kind: "document",
      name: "设计标准.md",
      relative_path: "设计标准.md",
      doc_type: "markdown",
      availability: "available",
      import_status: "not_imported",
      lifecycle_status: "awaiting_import",
    },
  ],
};

describe("ExternalMediaSourcesPanel", () => {
  beforeEach(() => {
    // vitest.config.ts enables restoreMocks, so the mocked implementation must be re-armed per test.
    vi.mocked(api.listExternalMediaEntries).mockResolvedValue(entries);
  });

  it("shows a remote child folder and keeps it read-only", async () => {
    const { ExternalFolderBrowser } = await import("./ExternalFolderBrowser");
    render(<ExternalFolderBrowser sourceId="source-1" title="共享目录远程目录" />);
    expect((await screen.findAllByText("第一章")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("共享").length).toBeGreaterThan(0);
    expect(screen.getByRole("columnheader", { name: "类型" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "资料" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "共享目录远程目录" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText(/共享只读/).length).toBeGreaterThan(0));
  });

  it("lists remote documents next to videos and marks un-imported files", async () => {
    const { ExternalFolderBrowser } = await import("./ExternalFolderBrowser");
    render(<ExternalFolderBrowser sourceId="source-1" title="共享目录远程目录" />);
    expect((await screen.findAllByText("设计标准.md")).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/未导入/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Markdown").length).toBeGreaterThan(0);
  });
});
