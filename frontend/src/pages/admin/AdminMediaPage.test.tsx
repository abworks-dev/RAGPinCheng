import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminMediaPage } from "./AdminMediaPage";

const mocks = vi.hoisted(() => ({
  listMediaAssets: vi.fn(),
  uploadMediaVideo: vi.fn(),
  uploadAutomaticMediaVideo: vi.fn(),
  listTranscriptionProfiles: vi.fn(),
  listTranscriptionJobs: vi.fn(),
  getTranscriptionJob: vi.fn(),
  cancelTranscriptionJob: vi.fn(),
  retryTranscription: vi.fn(),
}));

vi.mock("../../api/client", () => ({ api: mocks }));

const availableProfile = {
  profile_id: "funasr-sensevoice-zh-experimental-v1",
  display_name: "受控中文转录",
  description: "服务端白名单 Profile",
  qualification: "experimental",
  admission: "enabled",
  availability: "available",
  unavailable_reason_code: null,
  requires_review: true,
  auto_publish: false,
  auto_index: false,
};

const unavailableProfile = {
  ...availableProfile,
  profile_id: "disabled-profile",
  display_name: "不可用 Profile",
  availability: "unavailable",
  unavailable_reason_code: "service_unavailable",
};

const assets = [
  {
    media_id: "media-ready",
    title: "项目交付培训",
    original_filename: "delivery-training.mp4",
    mime_type: "video/mp4",
    file_size: 8 * 1024 * 1024,
    transcript_origin: "manual",
    status: "ready",
    created_at: 1785686400,
    updated_at: 1785686400,
    error: null,
  },
  {
    media_id: "media-failed",
    title: "失败示例",
    original_filename: "failed.mp4",
    mime_type: "video/mp4",
    file_size: 512,
    transcript_origin: "generated",
    status: "failed",
    created_at: 1785686400,
    updated_at: 1785686400,
    error: "转写格式不符合要求",
  },
];

const succeededJob = {
  job_id: "job-succeeded",
  media_id: "media-ready",
  attempt_number: 1,
  profile_id: availableProfile.profile_id,
  status: "succeeded" as const,
  stage: "formatting",
  processed_ms: 1000,
  total_ms: 1000,
  failure_error_code: null,
  error_summary: null,
  result_version_id: "version-1",
  created_at: 1,
  started_at: 2,
  finished_at: 3,
  updated_at: 3,
};

const failedJob = {
  ...succeededJob,
  job_id: "job-failed",
  media_id: "media-failed",
  status: "failed" as const,
  failure_error_code: "provider_unavailable",
  error_summary: "远端服务暂不可用",
  result_version_id: null,
};

describe("AdminMediaPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    let uuidSequence = 0;
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn(() => `11111111-1111-4111-8111-${String(++uuidSequence).padStart(12, "0")}`),
    });
    mocks.listMediaAssets.mockResolvedValue(assets);
    mocks.uploadMediaVideo.mockResolvedValue(assets[0]);
    mocks.uploadAutomaticMediaVideo.mockResolvedValue({ ...assets[0], transcription_job_id: "job-new" });
    mocks.listTranscriptionProfiles.mockResolvedValue([availableProfile, unavailableProfile]);
    mocks.listTranscriptionJobs.mockResolvedValue([succeededJob, failedJob]);
    mocks.getTranscriptionJob.mockResolvedValue({ ...succeededJob, job_id: "job-new" });
    mocks.cancelTranscriptionJob.mockResolvedValue({ ...succeededJob, status: "cancelled" });
    mocks.retryTranscription.mockResolvedValue({ ...failedJob, job_id: "job-retry", status: "pending" });
  });

  it("loads media assets and makes success semantics explicit", async () => {
    render(<AdminMediaPage />);
    expect(screen.getByLabelText("上传步骤")).toHaveTextContent("1. 上传视频");
    expect(await screen.findByText("项目交付培训")).toBeInTheDocument();
    expect(screen.getByText("共 2 个视频")).toBeInTheDocument();
    expect(screen.getByText("已就绪")).toHaveClass("bg-success/15");
    expect(screen.getByText("失败")).toHaveClass("bg-destructive/15");
    expect(screen.getByText("转录草稿已生成，等待人工审核；尚未发布，也未进入索引。")).toBeInTheDocument();
    expect(screen.getByText("远端服务暂不可用")).toBeInTheDocument();
  });

  it("renders when randomUUID is unavailable in an insecure HTTP context", async () => {
    vi.stubGlobal("crypto", {
      getRandomValues: vi.fn((bytes: Uint8Array) => {
        bytes.fill(7);
        return bytes;
      }),
    });

    render(<AdminMediaPage />);
    await addVideosAndOpenMode([video("one.mp4"), video("two.mp4")], "自动转录");

    expect(await screen.findByText("项目交付培训")).toBeInTheDocument();
    expect(screen.getByText("共 2 个视频")).toBeInTheDocument();
  });

  it("keeps the manual MP4 plus Markdown path unchanged", async () => {
    mocks.listMediaAssets.mockResolvedValueOnce([]).mockResolvedValueOnce(assets);
    const video = new File(["video-bytes"], "training.mp4", { type: "video/mp4" });
    const transcript = new File(["说话人 00:00:01 培训开始"], "training.md", { type: "text/markdown" });

    render(<AdminMediaPage />);
    await screen.findByText("暂无媒体资源");
    const uploadButton = screen.getByRole("button", { name: "上传视频与人工转写" });
    fireEvent.change(screen.getByLabelText("视频标题"), { target: { value: "  项目培训视频  " } });
    fireEvent.change(screen.getByLabelText("视频文件"), { target: { files: [video] } });
    expect(uploadButton).toBeDisabled();
    fireEvent.change(screen.getByLabelText("人工转写"), { target: { files: [transcript] } });
    fireEvent.click(uploadButton);

    await waitFor(() => expect(mocks.uploadMediaVideo).toHaveBeenCalledWith(video, transcript, "项目培训视频"));
    expect(mocks.uploadAutomaticMediaVideo).not.toHaveBeenCalled();
    expect(await screen.findByText("项目交付培训")).toBeInTheDocument();
  });

  it("uploads one MP4 with only a server profile in automatic mode", async () => {
    mocks.listMediaAssets.mockResolvedValueOnce([]).mockResolvedValueOnce(assets);
    const video = new File(["video-bytes"], "automatic.mp4", { type: "video/mp4" });

    render(<AdminMediaPage />);
    await screen.findByText("暂无媒体资源");
    fireEvent.click(screen.getByRole("button", { name: "自动转录" }));
    expect(await screen.findByLabelText("转录 Profile")).toHaveValue(availableProfile.profile_id);
    expect(screen.getByRole("option", { name: "不可用 Profile（不可用）" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("视频标题"), { target: { value: "自动转录视频" } });
    fireEvent.change(screen.getByLabelText("视频文件"), { target: { files: [video] } });
    fireEvent.click(screen.getByRole("button", { name: "上传并开始自动转录" }));

    await waitFor(() => expect(mocks.uploadAutomaticMediaVideo).toHaveBeenCalledWith(
      video,
      "自动转录视频",
      availableProfile.profile_id,
      expect.stringMatching(/^11111111-1111-4111-8111-/),
    ));
    expect(mocks.uploadMediaVideo).not.toHaveBeenCalled();
    expect(mocks.getTranscriptionJob).toHaveBeenCalledWith("job-new");
    expect(mocks.listTranscriptionJobs).toHaveBeenCalledTimes(1);
  });

  it("preserves upload fields and request identity after an automatic upload failure", async () => {
    mocks.listMediaAssets.mockResolvedValue([]);
    mocks.uploadAutomaticMediaVideo.mockRejectedValueOnce(new Error("上传服务暂不可用"));
    const video = new File(["video-bytes"], "automatic.mp4", { type: "video/mp4" });

    render(<AdminMediaPage />);
    await screen.findByText("暂无媒体资源");
    fireEvent.click(screen.getByRole("button", { name: "自动转录" }));
    fireEvent.change(screen.getByLabelText("视频标题"), { target: { value: "自动转录视频" } });
    fireEvent.change(screen.getByLabelText("视频文件"), { target: { files: [video] } });
    const uploadButton = screen.getByRole("button", { name: "上传并开始自动转录" });
    fireEvent.click(uploadButton);
    expect(await screen.findByRole("alert")).toHaveTextContent("上传服务暂不可用");
    fireEvent.click(uploadButton);

    await waitFor(() => expect(mocks.uploadAutomaticMediaVideo).toHaveBeenCalledTimes(2));
    expect(mocks.uploadAutomaticMediaVideo.mock.calls[0][3]).toBe(mocks.uploadAutomaticMediaVideo.mock.calls[1][3]);
  });

  it("cancels active jobs and retries failed jobs through the task API", async () => {
    const running = { ...succeededJob, job_id: "job-running", status: "running" as const, stage: "transcribing", processed_ms: 500 };
    mocks.listTranscriptionJobs.mockResolvedValue([running, failedJob]);
    mocks.cancelTranscriptionJob.mockResolvedValue({ ...running, status: "cancelled" });

    render(<AdminMediaPage />);
    await screen.findByText("项目交付培训");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    await waitFor(() => expect(mocks.cancelTranscriptionJob).toHaveBeenCalledWith("job-running"));
    await waitFor(() => expect(mocks.retryTranscription).toHaveBeenCalledWith(
      "media-failed",
      availableProfile.profile_id,
      expect.stringMatching(/^11111111-1111-4111-8111-/),
    ));
  });

  it("reuses the same retry idempotency key after a lost response", async () => {
    mocks.retryTranscription
      .mockRejectedValueOnce(new Error("响应丢失"))
      .mockRejectedValueOnce(new Error("响应仍不可用"));

    render(<AdminMediaPage />);
    await screen.findByText("项目交付培训");
    const retryButton = screen.getByRole("button", { name: "重试" });
    fireEvent.click(retryButton);
    expect(await screen.findByRole("alert")).toHaveTextContent("响应丢失");
    fireEvent.click(retryButton);

    await waitFor(() => expect(mocks.retryTranscription).toHaveBeenCalledTimes(2));
    expect(mocks.retryTranscription.mock.calls[0][2]).toBe(
      mocks.retryTranscription.mock.calls[1][2],
    );
  });

  it("surfaces media loading failure and retries", async () => {
    mocks.listMediaAssets.mockRejectedValueOnce(new Error("媒体服务暂不可用")).mockResolvedValueOnce(assets);
    render(<AdminMediaPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("媒体服务暂不可用");
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    await waitFor(() => expect(mocks.listMediaAssets).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("项目交付培训")).toBeInTheDocument();
  });
});
