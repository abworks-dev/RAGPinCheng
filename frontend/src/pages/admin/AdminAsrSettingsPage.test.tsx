import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AsrManagedProfile, AsrSettings, TranscriptionBase, TranscriptionScheme } from "../../types";
import { AdminAsrSettingsPage } from "./AdminAsrSettingsPage";

const mocks = vi.hoisted(() => ({ get: vi.fn(), bases: vi.fn(), schemes: vi.fn(), createScheme: vi.fn(), copyScheme: vi.fn(), updateScheme: vi.fn(), reorderSchemes: vi.fn(), requestRelease: vi.fn() }));

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

const bases: TranscriptionBase[] = [{
  id: "whisperx-v2", provider: "whisperx", model: "WhisperX full-decode v2", revision: "full-decode-v2",
  service_profile_id: "whisperx-large-v3-zh-align-v2", config_hash: "b".repeat(64), qualification: "qualification_approved",
  admission: "enabled", availability: "runtime", capabilities: { segmentation: true, decode_presets: true }, defaults: { segmentation_preset: "balanced" },
}];
const schemes: TranscriptionScheme[] = settings.profiles.map((item, index) => ({
  id: item.profile_id, name: item.display_name.replace(" v2", ""), description: item.description, base_id: "whisperx-v2",
  parameters: { segmentation_preset: item.segmentation?.preset || "natural", max_duration_ms: item.segmentation?.max_segment_duration_ms ?? null, max_chars: item.segmentation?.max_segment_chars || 500, merge_gap_ms: item.segmentation?.max_merge_gap_ms || 1000, terminology_profile: "bim-engineering-v1", prompt_asset: "asr_engineering_zh_v2", preprocessing_preset: "standard-audio-v1", vad_preset: "service-default-v1", decode_preset: "service-default-v1" },
  config_hash: item.application_config_hash, enabled: true, archived: false, system_preset: true, sort_order: index, version: 1, created_at: 1, updated_at: 1,
}));

describe("AdminAsrSettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue(settings);
    mocks.bases.mockResolvedValue(bases);
    mocks.schemes.mockResolvedValue(schemes);
    mocks.reorderSchemes.mockResolvedValue(schemes);
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

  it("lists ordered schemes and submits an idempotent release request", async () => {
    render(<AdminAsrSettingsPage />);
    expect(await screen.findByRole("heading", { name: "转录配置" })).toBeInTheDocument();
    expect(screen.getByText("WhisperX 工程转录 balanced")).toBeInTheDocument();
    expect(screen.getByText(/版本 1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "发布记录" }));
    fireEvent.change(screen.getByLabelText("发布 Profile"), { target: { value: settings.profiles[2].profile_id } });
    expect(screen.getByText("BIM-2026-0805")).toBeInTheDocument();
    expect(screen.getByText("Beam 10 · 温度 0.1")).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: "发布记录" }));
    expect(screen.getByRole("button", { name: "申请发布" })).toBeDisabled();
  });

  it("copies a preset and saves keyboard ordering with per-scheme versions", async () => {
    const copied = { ...schemes[0], id: "custom-copy", name: "自然分段副本", system_preset: false, version: 1, sort_order: 5 };
    mocks.copyScheme.mockResolvedValue(copied);
    render(<AdminAsrSettingsPage />);
    await screen.findByRole("heading", { name: "转录配置" });
    fireEvent.click(screen.getByRole("button", { name: /复制/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "副本名称" }), { target: { value: "自然分段副本" } });
    fireEvent.click(screen.getByRole("button", { name: "创建副本" }));
    await waitFor(() => expect(mocks.copyScheme).toHaveBeenCalledWith(schemes[0].id, { name: "自然分段副本" }));

    fireEvent.click(screen.getByRole("button", { name: /上移 WhisperX 工程转录 balanced/ }));
    fireEvent.click(screen.getByRole("button", { name: "保存顺序" }));
    await waitFor(() => expect(mocks.reorderSchemes).toHaveBeenCalledWith(expect.arrayContaining([
      { id: schemes[0].id, expected_version: 1 },
      { id: schemes[1].id, expected_version: 1 },
    ])));
  });
});
