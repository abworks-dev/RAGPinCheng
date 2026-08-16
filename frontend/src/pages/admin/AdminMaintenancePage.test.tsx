import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminMaintenancePage } from "./AdminMaintenancePage";

const mocks = vi.hoisted(() => ({
  status: vi.fn(),
  preview: vi.fn(),
  runs: vi.fn(),
  update: vi.fn(),
  cleanup: vi.fn(),
}));

vi.mock("../../api/client", () => ({ api: {
  adminMaintenance: mocks.status,
  adminMaintenancePreview: mocks.preview,
  adminMaintenanceRuns: mocks.runs,
  adminUpdateMaintenanceSettings: mocks.update,
  adminRunMaintenanceCleanup: mocks.cleanup,
} }));

const settings = {
  conversation_cleanup_enabled: true,
  conversation_retention_days: 30,
  updated_at: null,
  updated_by: null,
};
const preview = {
  retention_days: 30,
  conversations: 4,
  messages: 12,
  auth_sessions: 2,
  oldest_conversation_at: 1,
  newest_conversation_at: 2,
};

describe("AdminMaintenancePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.status.mockResolvedValue({ settings, sweeper_interval_seconds: 3600, last_run: null });
    mocks.preview.mockResolvedValue(preview);
    mocks.runs.mockResolvedValue({ runs: [] });
    mocks.update.mockResolvedValue(settings);
  });

  it("loads the persisted default policy and cleanup preview", async () => {
    render(<AdminMaintenancePage />);
    expect(await screen.findByRole("heading", { name: "系统维护" })).toBeInTheDocument();
    expect(screen.getByText("当前保留期").parentElement).toHaveTextContent("30 天");
    expect(screen.getByText("待清理对话").parentElement).toHaveTextContent("4");
    expect(screen.getByText("尚无清理记录")).toBeInTheDocument();
  });

  it("previews and confirms a shorter retention policy before saving", async () => {
    mocks.preview.mockResolvedValueOnce(preview).mockResolvedValueOnce({ ...preview, retention_days: 14, conversations: 9 });
    mocks.update.mockResolvedValue({ ...settings, conversation_retention_days: 14, updated_at: 10 });
    render(<AdminMaintenancePage />);
    await screen.findByRole("heading", { name: "系统维护" });
    fireEvent.change(screen.getByLabelText("保留期限"), { target: { value: "custom" } });
    fireEvent.change(screen.getByLabelText("自定义天数"), { target: { value: "14" } });
    fireEvent.click(screen.getByRole("button", { name: "保存策略" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("确认缩短保留期限");
    expect(screen.getByRole("dialog")).toHaveTextContent("9 条对话");
    fireEvent.click(screen.getByRole("button", { name: "确认保存" }));
    await waitFor(() => expect(mocks.update).toHaveBeenCalledWith(expect.objectContaining({ conversation_retention_days: 14 })));
  });

  it("requires an impact dialog before manual cleanup", async () => {
    mocks.cleanup.mockResolvedValue({
      run_id: 1, retention_days: 30, deleted_conversations: 4,
      deleted_messages: 12, deleted_auth_sessions: 2, started_at: 1, finished_at: 2,
    });
    render(<AdminMaintenancePage />);
    await screen.findByRole("heading", { name: "系统维护" });
    fireEvent.click(screen.getByRole("button", { name: "立即执行清理" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("删除后只能通过数据库备份恢复");
    fireEvent.click(screen.getByRole("button", { name: "确认永久删除" }));
    expect(await screen.findByText(/清理完成：删除 4 条对话/)).toBeInTheDocument();
    expect(mocks.cleanup).toHaveBeenCalledTimes(1);
  });

  it("shows duration and a sanitized reason for failed runs", async () => {
    mocks.runs.mockResolvedValue({ runs: [{
      id: 2, trigger_source: "automatic", status: "failed", retention_days: null,
      deleted_conversations: 0, deleted_messages: 0, deleted_auth_sessions: 0,
      started_at: 10, finished_at: 12, error_summary: "OperationalError",
    }] });
    render(<AdminMaintenancePage />);

    expect(await screen.findAllByText("耗时 2.0 秒")).not.toHaveLength(0);
    expect(screen.getAllByText("OperationalError")).not.toHaveLength(0);
    expect(screen.getAllByText("永久保留")).not.toHaveLength(0);
  });
});
