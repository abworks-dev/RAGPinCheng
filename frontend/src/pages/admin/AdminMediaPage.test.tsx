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

const secondProfile = {
  ...availableProfile,
  profile_id: "approved-profile",
  display_name: "正式中文转录",
  qualification: "qualified",
};

const unavailableProfile = {
  ...availableProfile,
  profile_id: "disabled-profile",
  display_name: "不可用 Profile",
  availability: "unavailable",
};

const assets = [{
  media_id: "media-ready",
  title: "项目交付培训",
  original_filename: "delivery-training.mp4",
  mime_type: "video/mp4",
  file_size: 1024,
  transcript_origin: "generated",
  status: "transcript_ready",
  review_status: "awaiting_review",
  publication_status: "not_published",
  index_status: null,
  created_at: 1785686400,
  updated_at: 1785686400,
  error: null,
}];

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

function video(name: string) {
  return new File(["video"], name, { type: "video/mp4" });
}

function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

async function addVideosAndOpenMode(files: File[], mode: "自动转录" | "人工转写") {
  fireEvent.change(screen.getByLabelText("选择视频文件"), { target: { files } });
  fireEvent.click(screen.getByRole("button", { name: "下一步：选择转写方式" }));
  fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${mode}`) }));
  await screen.findByLabelText("上传配置列表");
}

describe("AdminMediaPage wizard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    let sequence = 0;
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn(() => `11111111-1111-4111-8111-${String(++sequence).padStart(12, "0")}`),
    });
    mocks.listMediaAssets.mockResolvedValue(assets);
    mocks.uploadMediaVideo.mockResolvedValue(assets[0]);
    mocks.uploadAutomaticMediaVideo.mockImplementation(async (file: File) => ({
      ...assets[0],
      media_id: file.name,
      transcription_job_id: `job-${file.name}`,
    }));
    mocks.listTranscriptionProfiles.mockResolvedValue([availableProfile, secondProfile, unavailableProfile]);
    mocks.listTranscriptionJobs.mockResolvedValue([succeededJob]);
    mocks.getTranscriptionJob.mockResolvedValue(succeededJob);
    mocks.cancelTranscriptionJob.mockResolvedValue({ ...succeededJob, status: "cancelled" });
    mocks.retryTranscription.mockResolvedValue({ ...succeededJob, status: "pending" });
  });

  it("shows the three-step entry and keeps lifecycle states separate", async () => {
    render(<AdminMediaPage />);
    expect(screen.getByLabelText("上传步骤")).toHaveTextContent("1. 上传视频");
    expect(await screen.findByText("项目交付培训")).toBeInTheDocument();
    expect(screen.getByText("待人工审核")).toBeInTheDocument();
    expect(screen.getByText("未发布")).toBeInTheDocument();
    expect(screen.getByText("未开始")).toBeInTheDocument();
    expect(screen.getByText("转录草稿已生成；审核、发布与索引状态见右侧独立列。")).toBeInTheDocument();
  });

  it("accepts multiple MP4 files and applies one server profile to selected rows", async () => {
    render(<AdminMediaPage />);
    await addVideosAndOpenMode([video("one.mp4"), video("two.mp4")], "自动转录");

    expect(screen.getAllByText("实验性·强制审核", { exact: false }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("option", { name: /不可用 Profile/ }).every((option) => option.hasAttribute("disabled"))).toBe(true);
    fireEvent.change(screen.getByLabelText("批量转录 Profile"), { target: { value: secondProfile.profile_id } });
    fireEvent.click(screen.getByRole("button", { name: "应用到已选择视频" }));
    expect(screen.getByLabelText("one.mp4 的转录 Profile")).toHaveValue(secondProfile.profile_id);
    expect(screen.getByLabelText("two.mp4 的转录 Profile")).toHaveValue(secondProfile.profile_id);

    fireEvent.click(screen.getByRole("button", { name: "上传并创建自动转录任务" }));
    await waitFor(() => expect(mocks.uploadAutomaticMediaVideo).toHaveBeenCalledTimes(2));
    expect(mocks.uploadAutomaticMediaVideo.mock.calls[0][2]).toBe(secondProfile.profile_id);
    expect(mocks.uploadAutomaticMediaVideo.mock.calls[0][3]).not.toBe(mocks.uploadAutomaticMediaVideo.mock.calls[1][3]);
  });

  it("allows each automatic row to override the batch profile", async () => {
    render(<AdminMediaPage />);
    await addVideosAndOpenMode([video("one.mp4"), video("two.mp4")], "自动转录");
    fireEvent.change(screen.getByLabelText("two.mp4 的转录 Profile"), { target: { value: secondProfile.profile_id } });
    fireEvent.click(screen.getByRole("button", { name: "上传并创建自动转录任务" }));

    await waitFor(() => expect(mocks.uploadAutomaticMediaVideo).toHaveBeenCalledTimes(2));
    const calls = mocks.uploadAutomaticMediaVideo.mock.calls;
    expect(calls.find((call) => call[0].name === "one.mp4")?.[2]).toBe(availableProfile.profile_id);
    expect(calls.find((call) => call[0].name === "two.mp4")?.[2]).toBe(secondProfile.profile_id);
  });

  it("keeps manual Markdown per video and uploads edited text through the existing path", async () => {
    render(<AdminMediaPage />);
    await addVideosAndOpenMode([video("manual.mp4")], "人工转写");
    const transcript = new File(["原稿"], "manual.md", { type: "text/markdown" });
    Object.defineProperty(transcript, "text", { value: vi.fn().mockResolvedValue("说话人 1 00:00:01 原稿") });
    fireEvent.change(screen.getByLabelText("manual.mp4 的人工转写"), { target: { files: [transcript] } });
    await screen.findByRole("button", { name: "打开并编辑" });
    fireEvent.click(screen.getByRole("button", { name: "打开并编辑" }));
    fireEvent.change(screen.getByLabelText("Markdown 转写内容"), { target: { value: "说话人 1 00:00:01 编辑后" } });
    fireEvent.click(screen.getByRole("button", { name: "保存并关闭" }));
    fireEvent.click(screen.getByRole("button", { name: "上传视频与人工转写" }));

    await waitFor(() => expect(mocks.uploadMediaVideo).toHaveBeenCalledTimes(1));
    expect(mocks.uploadAutomaticMediaVideo).not.toHaveBeenCalled();
    const uploadedTranscript = mocks.uploadMediaVideo.mock.calls[0][1] as File;
    expect(await readFile(uploadedTranscript)).toBe("说话人 1 00:00:01 编辑后");
  });

  it("retains failed batch rows for a retry with the same idempotency key", async () => {
    mocks.uploadAutomaticMediaVideo
      .mockRejectedValueOnce(new Error("上传服务暂不可用"))
      .mockResolvedValueOnce({ ...assets[0], transcription_job_id: "job-new" });
    render(<AdminMediaPage />);
    await addVideosAndOpenMode([video("retry.mp4")], "自动转录");
    const submit = screen.getByRole("button", { name: "上传并创建自动转录任务" });
    fireEvent.click(submit);
    expect(await screen.findByText("上传服务暂不可用")).toBeInTheDocument();
    const firstKey = mocks.uploadAutomaticMediaVideo.mock.calls[0][3];
    fireEvent.click(submit);
    await waitFor(() => expect(mocks.uploadAutomaticMediaVideo).toHaveBeenCalledTimes(2));
    expect(mocks.uploadAutomaticMediaVideo.mock.calls[1][3]).toBe(firstKey);
  });

  it("preserves cancel and retry recovery actions", async () => {
    const running = { ...succeededJob, status: "running" as const };
    const failed = { ...succeededJob, job_id: "job-failed", media_id: "media-failed", status: "failed" as const };
    mocks.listMediaAssets.mockResolvedValue([assets[0], { ...assets[0], media_id: "media-failed", title: "失败视频" }]);
    mocks.listTranscriptionJobs.mockResolvedValue([running, failed]);
    render(<AdminMediaPage />);
    await screen.findByText("项目交付培训");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(mocks.cancelTranscriptionJob).toHaveBeenCalledWith("job-succeeded"));
    await waitFor(() => expect(mocks.retryTranscription).toHaveBeenCalledWith("media-failed", availableProfile.profile_id, expect.any(String)));
  });
});
