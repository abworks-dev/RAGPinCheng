import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminAnswerPolicyPage } from "./AdminAnswerPolicyPage";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  update: vi.fn(),
  reset: vi.fn(),
  audit: vi.fn(),
}));

vi.mock("../../api/admin/answerPolicy", () => ({
  adminAnswerPolicyApi: mocks,
}));

const policy = {
  answer_temperature: 0.2,
  answer_max_output_tokens: 1200,
  answer_context_chars: 6000,
  relevance_gate_enabled: false,
  relevance_min_score: 0,
  relevance_min_rrf: 0,
  relevance_min_margin: 0,
  policy_version: "env-default-v1",
  updated_at: null,
  updated_by: null,
};

describe("AdminAnswerPolicyPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue(policy);
    mocks.audit.mockResolvedValue({ entries: [] });
    mocks.update.mockResolvedValue({ ...policy, policy_version: "admin-2" });
    mocks.reset.mockResolvedValue(policy);
  });

  it("loads global settings and requires a reason before enabling the gate", async () => {
    render(<AdminAnswerPolicyPage />);
    expect(await screen.findByRole("heading", { name: "回答策略" })).toBeInTheDocument();
    expect(screen.getByText("范围 256 至 4096，默认 1200。")).toBeInTheDocument();
    expect(screen.getByText("范围 2000 至 12000，默认 6000，包含检索资料预算。")).toBeInTheDocument();
    expect(screen.getByText("第一名资料的重排相关性分数；低于阈值时拦截。")).toBeInTheDocument();
    expect(screen.getByText("第一名资料的混合检索融合分数；低于阈值时拦截。")).toBeInTheDocument();
    expect(screen.getByText("第一名与第二名的重排分数差；低于阈值时拦截。")).toBeInTheDocument();
    const gate = screen.getByRole("checkbox", { name: /启用低相关性回答拦截/ });
    fireEvent.click(gate);
    fireEvent.click(screen.getByRole("button", { name: "保存策略" }));
    expect(await screen.findByRole("heading", { name: "确认开启相关性保护" })).toBeInTheDocument();
    expect(mocks.update).not.toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText("例如：完成线上误答问题的阈值校准"), { target: { value: "线上校准" } });
    fireEvent.click(screen.getByRole("button", { name: "确认开启并保存" }));
    await waitFor(() => expect(mocks.update).toHaveBeenCalledWith(expect.objectContaining({ relevance_gate_enabled: true, change_reason: "线上校准" })));
  });

  it("can restore defaults", async () => {
    render(<AdminAnswerPolicyPage />);
    expect(await screen.findByRole("heading", { name: "回答策略" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "恢复默认" }));
    await waitFor(() => expect(mocks.reset).toHaveBeenCalledTimes(1));
  });
});
