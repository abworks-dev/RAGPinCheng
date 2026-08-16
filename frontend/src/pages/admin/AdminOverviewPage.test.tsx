import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminOverviewPage } from "./AdminOverviewPage";

const mocks = vi.hoisted(() => ({
  adminStats: vi.fn(),
  adminMaintenance: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  api: {
    adminStats: mocks.adminStats,
    adminMaintenance: mocks.adminMaintenance,
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
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the loading state while requesting statistics", () => {
    mocks.adminStats.mockReturnValue(new Promise(() => undefined));

    render(<AdminOverviewPage />);

    expect(screen.getByText("正在加载管理概览…")).toBeInTheDocument();
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

    expect(await screen.findByRole("alert")).toHaveTextContent("概览加载失败");
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
});
