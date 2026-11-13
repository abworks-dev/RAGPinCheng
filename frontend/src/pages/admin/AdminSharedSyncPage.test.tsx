import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminSharedSyncPage } from "./AdminSharedSyncPage";

const mocks = vi.hoisted(() => ({
  status: vi.fn(),
  runs: vi.fn(),
  events: vi.fn(),
  withdrawals: vi.fn(),
  restore: vi.fn(),
  runNow: vi.fn(),
}));

vi.mock("../../api/client", () => ({ api: {
  adminSharedSyncStatus: mocks.status,
  adminSharedSyncRuns: mocks.runs,
  adminSharedSyncEvents: mocks.events,
  adminSharedSyncWithdrawals: mocks.withdrawals,
  adminSharedSyncRestore: mocks.restore,
  adminSharedSyncRun: mocks.runNow,
} }));

const counts = {
  discovered_count: 3, added_count: 1, changed_count: 0, missing_count: 1,
  imported_count: 1, published_count: 1, withdrawn_count: 1, restored_count: 0,
  purged_count: 0, skipped_count: 0, failed_count: 0,
};

const status = {
  enabled: true,
  phase: "day" as const,
  next_transition: 1_800_000_000,
  sources: [{
    id: "source-1",
    name: "BIM 共享目录",
    enabled: true,
    state: {
      entries_total: 12, entries_available: 11, entries_missing: 1, entries_superseded: 0,
      imported: 10, import_failed: 1, withdrawn: 1,
      last_run: {
        id: "run-1", source_id: "source-1", source_name: "BIM 共享目录", trigger: "nightly" as const,
        status: "succeeded" as const, counts, error_code: null, started_at: 1_700_000_000, finished_at: 1_700_000_090,
      },
    },
  }],
};

const withdrawals = [{
  id: "w-1",
  item_id: "item-1",
  version_id: "version-1",
  source_id: "source-1",
  relative_path: "项目资料/设计说明.md",
  reason: "shared_missing" as const,
  state: "withdrawn" as const,
  sync_generation: 3,
  created_at: 1_700_000_000,
  restored_at: null,
  purged_at: null,
}];

describe("AdminSharedSyncPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.status.mockResolvedValue(status);
    mocks.runs.mockResolvedValue([{
      id: "run-1", source_id: "source-1", source_name: "BIM 共享目录", trigger: "nightly",
      status: "succeeded", counts, error_code: null, started_at: 1_700_000_000, finished_at: 1_700_000_090,
    }]);
    mocks.events.mockResolvedValue([{
      id: "e-1", run_id: "run-1", source_id: "source-1", entry_id: "entry-1",
      relative_path: "项目资料/设计说明.md", action: "withdrawn", item_id: "item-1",
      version_id: "version-1", previous_version_id: null, error_code: null, detail: null,
      created_at: 1_700_000_000,
    }]);
    mocks.withdrawals.mockResolvedValue(withdrawals);
    mocks.restore.mockResolvedValue({ ...withdrawals[0], state: "restored" });
    mocks.runNow.mockResolvedValue({ phase: "day", reaped: 0, pruned: {}, results: [{ status: "succeeded" }] });
  });

  it("shows the current phase, sources, withdrawals and events", async () => {
    render(<AdminSharedSyncPage />);
    expect(await screen.findByRole("heading", { name: "共享目录同步" })).toBeInTheDocument();
    expect(screen.getByText(/日间（只登记、不自动发布）/)).toBeInTheDocument();
    expect(screen.getAllByText("BIM 共享目录").length).toBeGreaterThan(0);
    expect(screen.getByText("共享端文件缺失")).toBeInTheDocument();
    expect(screen.getByText("已暂撤，可恢复")).toBeInTheDocument();
    expect(screen.getByText("已撤回")).toBeInTheDocument();
  });

  it("restores a withdrawn version and refreshes the lists", async () => {
    render(<AdminSharedSyncPage />);
    fireEvent.click(await screen.findByRole("button", { name: "恢复原版本" }));
    await waitFor(() => expect(mocks.restore).toHaveBeenCalledWith("w-1"));
    await waitFor(() => expect(mocks.withdrawals).toHaveBeenCalledTimes(2));
  });

  it("runs a sync now and reports how many sources were processed", async () => {
    render(<AdminSharedSyncPage />);
    fireEvent.click(await screen.findByRole("button", { name: "立即同步一次" }));
    await waitFor(() => expect(mocks.runNow).toHaveBeenCalledWith(null));
  });

  it("surfaces a load failure with a retry action", async () => {
    mocks.status.mockRejectedValueOnce(new Error("状态服务不可用"));
    render(<AdminSharedSyncPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("共享同步状态加载失败");
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});
