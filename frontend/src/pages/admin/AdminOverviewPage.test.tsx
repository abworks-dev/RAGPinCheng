import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminOverviewPage } from "./AdminOverviewPage";

const mocks = vi.hoisted(() => ({
  adminStats: vi.fn(),
  adminMaintenance: vi.fn(),
  adminSystemOverview: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  api: {
    adminStats: mocks.adminStats,
    adminMaintenance: mocks.adminMaintenance,
    adminSystemOverview: mocks.adminSystemOverview,
  },
}));

const initialStats = {
  users_total: 12,
  users_active: 10,
  conversations_total: 24,
  conversations_7d: 6,
  messages_total: 96,
  messages_7d: 18,
};

describe("AdminOverviewPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.adminMaintenance.mockResolvedValue({
      settings: { conversation_cleanup_enabled: true, conversation_retention_days: null, updated_at: null, updated_by: null },
      sweeper_interval_seconds: 3600,
      last_run: { id: 1, trigger_source: "automatic", status: "succeeded", retention_days: null, deleted_conversations: 0, deleted_messages: 0, deleted_auth_sessions: 2, started_at: 10, finished_at: 11, error_summary: null },
    });
    mocks.adminSystemOverview.mockResolvedValue({
      topology: "separate",
      checked_at: 20,
      app: { status: "healthy", cpu_percent: 31.2, memory_used_bytes: 4 * 1024 ** 3, memory_total_bytes: 16 * 1024 ** 3, disk_used_bytes: 40 * 1024 ** 3, disk_total_bytes: 100 * 1024 ** 3, checked_at: 20, error_code: null },
      gpu: { status: "healthy", model_loaded: true, device_name: "合成 GPU", vram_used_bytes: 4 * 1024 ** 3, vram_total_bytes: 16 * 1024 ** 3, utilization_percent: 42, temperature_celsius: 53, inflight_requests: 1, checked_at: 20, data_age_seconds: 0, stale: false, error_code: null },
      office_processing: { enabled: true, mode: "deployment_config", disabled_reason: null, status: "healthy", checked_at: 20, error_code: null },
      external_usage: { today: {}, month: {}, all: {} },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the loading state while requesting statistics", () => {
    mocks.adminStats.mockReturnValue(new Promise(() => undefined));

    render(<AdminOverviewPage />);

    expect(screen.getByText("正在加载系统概览…")).toBeInTheDocument();
  });

  it("renders the six existing statistics without changing their order", async () => {
    mocks.adminStats.mockResolvedValue(initialStats);

    render(<AdminOverviewPage />);

    const labels = ["用户总数", "启用用户", "对话总数", "对话（近 7 天）", "消息总数", "消息（近 7 天）"];
    await screen.findByText("用户总数");
    const section = screen.getByRole("heading", { name: "核心指标" }).parentElement?.parentElement;

    expect(section).not.toBeNull();
    const sectionText = section?.textContent ?? "";
    let previousPosition = -1;
    for (const label of labels) {
      const position = sectionText.indexOf(label);
      expect(position).toBeGreaterThan(previousPosition);
      previousPosition = position;
    }
    expect(mocks.adminStats).toHaveBeenCalledTimes(1);
  });

  it("surfaces a statistics loading failure", async () => {
    mocks.adminStats.mockRejectedValue(new Error("统计服务暂不可用"));

    render(<AdminOverviewPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("系统概览加载失败");
    expect(screen.getByRole("alert")).toHaveTextContent("统计服务暂不可用");
  });

  it("keeps destructive maintenance actions out of the overview", async () => {
    mocks.adminStats.mockResolvedValue(initialStats);
    render(<AdminOverviewPage />);
    await screen.findByText("用户总数");
    expect(screen.queryByRole("button", { name: /清理/ })).not.toBeInTheDocument();
  });

  it("shows a compact maintenance summary and opens the maintenance tab", async () => {
    const onOpenMaintenance = vi.fn();
    mocks.adminStats.mockResolvedValue(initialStats);
    render(<AdminOverviewPage onOpenMaintenance={onOpenMaintenance} />);

    expect(await screen.findByText("永久保留")).toBeInTheDocument();
    expect(screen.getByText("已启用")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看系统维护" }));
    expect(onOpenMaintenance).toHaveBeenCalledTimes(1);
  });

  it("keeps core statistics visible when maintenance status fails", async () => {
    mocks.adminStats.mockResolvedValue(initialStats);
    mocks.adminMaintenance.mockRejectedValue(new Error("维护状态暂不可用"));
    render(<AdminOverviewPage />);

    expect(await screen.findByText("用户总数")).toBeInTheDocument();
    expect(await screen.findByText("维护状态暂不可用")).toBeInTheDocument();
  });

  it("shows production runtime status without exposing technical node ids", async () => {
    mocks.adminStats.mockResolvedValue(initialStats);
    render(<AdminOverviewPage />);

    expect(await screen.findByRole("heading", { name: "生产运行状态" })).toBeInTheDocument();
    expect(screen.getByText("分离部署")).toBeInTheDocument();
    expect(screen.getByText("合成 GPU")).toBeInTheDocument();
    expect(screen.queryByText(/node|节点 ID/i)).not.toBeInTheDocument();
    const officeHeading = screen.getByText("Office 新资料处理");
    expect(officeHeading).toBeInTheDocument();
    expect(within(officeHeading.parentElement!).getByText("运行正常")).toBeInTheDocument();
  });

  it("shows when new Office processing is disabled without hiding existing-content scope", async () => {
    mocks.adminStats.mockResolvedValue(initialStats);
    mocks.adminSystemOverview.mockResolvedValue({
      topology: "unknown",
      checked_at: 20,
      app: { status: "healthy", cpu_percent: null, memory_used_bytes: null, memory_total_bytes: null, disk_used_bytes: null, disk_total_bytes: null, checked_at: 20, error_code: null },
      gpu: { status: "unavailable", model_loaded: null, device_name: null, vram_used_bytes: null, vram_total_bytes: null, utilization_percent: null, temperature_celsius: null, inflight_requests: null, checked_at: 20, data_age_seconds: null, stale: false, error_code: "gpu_metrics_unreachable" },
      office_processing: { enabled: false, mode: "deployment_config", disabled_reason: "office_processing_disabled", status: "disabled", checked_at: 20, error_code: null },
      external_usage: { today: {}, month: {}, all: {} },
    });
    render(<AdminOverviewPage />);
    expect(await screen.findByText("Office 新资料处理")).toBeInTheDocument();
    expect(screen.getByText("已停用")).toBeInTheDocument();
    expect(screen.getByText(/既有资料仍可检索/)).toBeInTheDocument();
  });

  it("shows when the Office conversion service is unreachable", async () => {
    mocks.adminStats.mockResolvedValue(initialStats);
    mocks.adminSystemOverview.mockResolvedValue({
      topology: "unknown",
      checked_at: 20,
      app: { status: "healthy", cpu_percent: null, memory_used_bytes: null, memory_total_bytes: null, disk_used_bytes: null, disk_total_bytes: null, checked_at: 20, error_code: null },
      gpu: { status: "unavailable", model_loaded: null, device_name: null, vram_used_bytes: null, vram_total_bytes: null, utilization_percent: null, temperature_celsius: null, inflight_requests: null, checked_at: 20, data_age_seconds: null, stale: false, error_code: "gpu_metrics_unreachable" },
      office_processing: { enabled: true, mode: "deployment_config", disabled_reason: null, status: "unavailable", checked_at: 20, error_code: "office_service_unreachable" },
    });
    render(<AdminOverviewPage />);
    expect(await screen.findByText("服务异常")).toBeInTheDocument();
    expect(screen.getByText(/PPTX 预览生成可能失败/)).toBeInTheDocument();
  });

  it("keeps the overview usable when runtime metrics fail", async () => {
    mocks.adminStats.mockResolvedValue(initialStats);
    mocks.adminSystemOverview.mockRejectedValue(new Error("GPU 指标暂不可用"));
    render(<AdminOverviewPage />);

    expect(await screen.findByText("用户总数")).toBeInTheDocument();
    expect(await screen.findByText("生产运行状态暂不可用：GPU 指标暂不可用")).toBeInTheDocument();
  });
});
