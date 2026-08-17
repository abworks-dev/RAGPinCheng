import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AsrManagedProfile, AsrSettings } from "../../types";
import { AdminAsrSettingsPage } from "./AdminAsrSettingsPage";

const mocks = vi.hoisted(() => ({ get: vi.fn(), requestRelease: vi.fn() }));

vi.mock("../../api/admin/asr", () => ({ adminAsrApi: mocks }));
vi.mock("../../components/ui/toast", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

function profile(preset: "natural" | "balanced" | "fine", seconds: number | null): AsrManagedProfile {
  return {
    profile_id: `whisperx-large-v3-zh-${preset}-v2`,
    display_name: `WhisperX 工程转录 ${preset} v2`,
    description: `${preset} 合成配置`,
    profile_version: "2",
    application_config_hash: preset.repeat(64).slice(0, 64),
    qualification: "qualification_approved",
    admission: "enabled",
    availability: "available",
    unavailable_reason_code: null,
    release_eligible: true,
    segmentation: {
      preset,
      max_segment_duration_ms: seconds === null ? null : seconds * 1000,
      max_segment_chars: preset === "fine" ? 120 : 240,
      max_merge_gap_ms: preset === "fine" ? 500 : preset === "balanced" ? 750 : 1000,
    },
    terminology_rule_set: "bim-engineering-v1",
    protected_terms: ["Revit", "Navisworks", "AutoCAD", "BIM", "BIM-2026-0805", "12.5", "208", "95%"],
    decode: {
      service_profile_id: "whisperx-large-v3-zh-align-v2",
      model_name: "Whisper large-v3 + 中文对齐",
      beam_size: 10,
      temperature: 0.1,
      hotword_count: 20,
      prompt_asset_id: "asr_engineering_zh_v2",
      service_profile_config_hash: "a".repeat(64),
      qualification_policy: "whisperx-r3/1",
    },
  };
}

const settings: AsrSettings = {
  service: { status: "healthy", queue_depth: 0, queue_limit: 8, pause_reason: null },
  profiles: [profile("natural", null), profile("balanced", 30), profile("fine", 15)],
  release_requests: [],
  audit_events: [],
};

describe("AdminAsrSettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue(settings);
    mocks.requestRelease.mockResolvedValue({
      request_id: "11111111-1111-4111-8111-111111111111",
      profile_id: settings.profiles[1].profile_id,
      profile_display_name: settings.profiles[1].display_name,
      profile_config_hash: settings.profiles[1].application_config_hash,
      status: "requested",
      request_reason: "常规培训视频",
      requested_by_name: "系统管理员",
      created_at: 1700000000,
      updated_at: 1700000000,
    });
  });

  it("compares server-owned segmentation presets and submits an idempotent release request", async () => {
    render(<AdminAsrSettingsPage />);
    expect(await screen.findByRole("heading", { name: "转录配置" })).toBeInTheDocument();
    expect(screen.getByText("30 秒")).toBeInTheDocument();
    expect(screen.getByText("BIM-2026-0805")).toBeInTheDocument();
    expect(screen.getByText("Beam 10 · 温度 0.1")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /细分/ }));
    expect(screen.getByText("15 秒")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "申请发布" }));
    fireEvent.change(screen.getByPlaceholderText("例如：培训视频需要更密集的时间定位"), { target: { value: "常规培训视频" } });
    fireEvent.click(screen.getByRole("button", { name: "确认申请" }));

    await waitFor(() => expect(mocks.requestRelease).toHaveBeenCalledWith(expect.objectContaining({
      profile_id: settings.profiles[2].profile_id,
      request_idempotency_key: expect.stringMatching(/^[0-9a-f-]{36}$/),
      request_reason: "常规培训视频",
    })));
    expect(await screen.findByText("待发布处理")).toBeInTheDocument();
  });

  it("keeps release disabled when the runtime identity is not eligible", async () => {
    mocks.get.mockResolvedValue({
      ...settings,
      service: { status: "degraded", queue_depth: 1, queue_limit: 8, pause_reason: "bge_busy" },
      profiles: settings.profiles.map((item) => ({ ...item, release_eligible: false })),
    });
    render(<AdminAsrSettingsPage />);
    expect((await screen.findAllByText("服务受限")).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "申请发布" })).toBeDisabled();
  });
});
