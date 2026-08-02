import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminOverviewPage } from "./AdminOverviewPage";

const mocks = vi.hoisted(() => ({
  adminStats: vi.fn(),
  adminSweep: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  api: {
    adminStats: mocks.adminStats,
    adminSweep: mocks.adminSweep,
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

  it("does not sweep when the administrator cancels confirmation", async () => {
    mocks.adminStats.mockResolvedValue(initialStats);
    vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<AdminOverviewPage />);
    await screen.findByText("用户总数");
    fireEvent.click(screen.getByRole("button", { name: "立即清理过期对话" }));

    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(mocks.adminSweep).not.toHaveBeenCalled();
    expect(mocks.adminStats).toHaveBeenCalledTimes(1);
  });

  it("keeps the sweep call, refreshes statistics, and reports success", async () => {
    mocks.adminStats.mockResolvedValueOnce(initialStats).mockResolvedValueOnce({ ...initialStats, conversations_total: 20 });
    mocks.adminSweep.mockResolvedValue({ deleted_conversations: 4, deleted_auth_sessions: 2 });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<AdminOverviewPage />);
    await screen.findByText("用户总数");
    fireEvent.click(screen.getByRole("button", { name: "立即清理过期对话" }));

    expect(await screen.findByText("已删除 4 条对话、2 条登录会话。")).toBeInTheDocument();
    await waitFor(() => expect(mocks.adminStats).toHaveBeenCalledTimes(2));
    expect(mocks.adminSweep).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("status")).toHaveTextContent("清理完成");
  });
});
