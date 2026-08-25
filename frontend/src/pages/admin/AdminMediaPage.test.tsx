import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminMediaPage } from "./AdminMediaPage";

const mocks = vi.hoisted(() => ({
  listMediaAssets: vi.fn(),
  deleteFailedMediaAsset: vi.fn(),
  uploadMediaVideo: vi.fn(),
  uploadAutomaticMediaVideo: vi.fn(),
  uploadReplacementMediaVideo: vi.fn(),
  listTranscriptionProfiles: vi.fn(),
  listTranscriptionSchemes: vi.fn(),
  listTranscriptionJobs: vi.fn(),
  listTranscriptVersions: vi.fn(),
  getTranscriptionJob: vi.fn(),
  cancelTranscriptionJob: vi.fn(),
  retryTranscription: vi.fn(),
  bulkRetryTranscriptions: vi.fn(),
  bulkDeleteFailedMediaAssets: vi.fn(),
  managedCategories: vi.fn(),
  preflightMediaUpload: vi.fn(),
  moveManagedContent: vi.fn(),
  listExternalMediaRoots: vi.fn(),
  listExternalMediaSources: vi.fn(),
  createExternalMediaSource: vi.fn(),
  updateExternalMediaSource: vi.fn(),
  scanExternalMediaSource: vi.fn(),
  listExternalMediaEntries: vi.fn(),
  enqueueExternalMedia: vi.fn(),
}));
const scrollIntoView = vi.fn();

vi.mock("../../api/client", () => ({ api: mocks }));

const availableProfile = {
  scheme_id: "funasr-sensevoice-zh-experimental-v1",
  name: "受控中文转录",
  description: "服务端受控转录方案",
  base_id: "sensevoice-v1",
  config_hash: "a".repeat(64),
  enabled: true,
  archived: false,
  sort_order: 0,
  version: 1,
  availability: "available",
  unavailable_reason_code: null,
  requires_review: true,
  auto_publish: false,
  auto_index: false,
};

const secondProfile = {
  ...availableProfile,
  scheme_id: "approved-profile",
  name: "正式中文转录",
  sort_order: 1,
};

const unavailableProfile = {
  ...availableProfile,
  scheme_id: "disabled-profile",
  name: "不可用方案",
  sort_order: 2,
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
  available_actions: ["review_transcript"],
  disabled_actions: {},
}];

const succeededJob = {
  job_id: "job-succeeded",
  media_id: "media-ready",
  attempt_number: 1,
  profile_id: availableProfile.scheme_id,
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
  fireEvent.click(screen.getByRole("button", { name: /上传视频/ }));
  fireEvent.change(screen.getByLabelText("选择视频文件"), { target: { files } });
  fireEvent.click(screen.getByRole("button", { name: "下一步：选择转写方式" }));
  fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${mode}`) }));
  await screen.findByLabelText("上传配置列表");
}

describe("AdminMediaPage wizard", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    delete (Element.prototype as { scrollIntoView?: Element["scrollIntoView"] }).scrollIntoView;
  });

  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    window.history.replaceState({}, "", "/admin/media");
    let sequence = 0;
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn(() => `11111111-1111-4111-8111-${String(++sequence).padStart(12, "0")}`),
    });
    mocks.listMediaAssets.mockResolvedValue(assets);
    mocks.deleteFailedMediaAsset.mockResolvedValue({ media_id: "media-ready", cleanup_mode: "deleted" });
    mocks.uploadMediaVideo.mockResolvedValue(assets[0]);
    mocks.uploadAutomaticMediaVideo.mockImplementation(async (file: File) => ({
      ...assets[0],
      media_id: file.name,
      transcription_job_id: `job-${file.name}`,
    }));
    mocks.uploadReplacementMediaVideo.mockResolvedValue({
      ...assets[0],
      media_id: "media-replacement-candidate",
      transcription_job_id: "job-replacement",
    });
    mocks.listTranscriptionProfiles.mockResolvedValue([]);
    mocks.listTranscriptionSchemes.mockResolvedValue([availableProfile, secondProfile, unavailableProfile]);
    mocks.listTranscriptionJobs.mockResolvedValue([succeededJob]);
    mocks.listTranscriptVersions.mockResolvedValue([]);
    mocks.getTranscriptionJob.mockResolvedValue(succeededJob);
    mocks.cancelTranscriptionJob.mockResolvedValue({ ...succeededJob, status: "cancelled" });
    mocks.retryTranscription.mockResolvedValue({ ...succeededJob, status: "pending" });
    mocks.bulkRetryTranscriptions.mockResolvedValue({ items: [], succeeded: 0, failed: 0 });
    mocks.bulkDeleteFailedMediaAssets.mockResolvedValue({ items: [], succeeded: 0, failed: 0 });
    mocks.managedCategories.mockResolvedValue([{
      id: "cat-05", category_key: "training", parent_id: null, display_code: "05",
      display_name: "培训资料", sort_order: 50, level: 1, is_active: true,
      full_path: "05 培训资料", item_count: 0, version: 1,
    }]);
    mocks.preflightMediaUpload.mockImplementation(async (body: { category_id: string; items: Array<{ client_id: string }> }) => ({
      category_id: body.category_id,
      entries: body.items.map((item) => ({ client_id: item.client_id, status: "ready", suggested_title: null, suggested_filename: null, conflicts: [] })),
    }));
    mocks.moveManagedContent.mockResolvedValue({});
    mocks.listExternalMediaRoots.mockResolvedValue([]);
    mocks.listExternalMediaSources.mockResolvedValue([]);
    mocks.listExternalMediaEntries.mockResolvedValue({ source_id: "", parent_relative_path: "", entries: [] });
    mocks.scanExternalMediaSource.mockResolvedValue({ run_id: "scan-1", source_id: "source-1", discovered_count: 2, added_count: 2, changed_count: 0, missing_count: 0, enqueued_count: 0, enqueue_failures: 0 });
    mocks.enqueueExternalMedia.mockResolvedValue({ requested: 1, enqueued: 1, failed: 0, failures: {} });
  });

  it("moves shared media configuration out of the transcription task page", async () => {
    render(<AdminMediaPage />);
    expect(await screen.findByText("视频资源")).toBeInTheDocument();
    expect(screen.queryByText("共享资料源")).not.toBeInTheDocument();
    expect(screen.queryByText("资料管理 / 转录任务")).not.toBeInTheDocument();
  });

  it("shows the three-step entry and keeps lifecycle states separate", async () => {
    render(<AdminMediaPage />);
    fireEvent.click(screen.getByRole("button", { name: /上传视频/ }));
    expect(screen.getByLabelText("上传步骤")).toHaveTextContent("1. 上传视频");
    expect(await screen.findByText("项目交付培训")).toBeInTheDocument();
    expect(screen.getByText("待人工审核")).toBeInTheDocument();
    expect(screen.getByText("未发布")).toBeInTheDocument();
    expect(screen.getByText("未开始")).toBeInTheDocument();
    expect(screen.getByText("草稿已生成，等待后续审核与发布。")).toBeInTheDocument();
  });

  it("opens the requested workbench from a library deep link and clears it on close", async () => {
    window.history.replaceState({}, "", "/admin/media?media_id=media-ready&workbench=1");
    render(<AdminMediaPage />);

    const workbench = await screen.findByRole("dialog", { name: "项目交付培训" });
    expect(workbench).toBeInTheDocument();
    fireEvent.click(within(workbench).getByRole("button", { name: "关闭转写工作台" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "项目交付培训" })).not.toBeInTheDocument());
    expect(window.location.search).toBe("");
  });

  it("opens replacement from a library deep link and reports upload versus server processing", async () => {
    let callbacks: { onProgress: (progress: { loaded: number; total: number; ratio: number }) => void; onUploaded: () => void } | undefined;
    let finishUpload: ((value: typeof assets[number] & { media_id: string; transcription_job_id: string }) => void) | undefined;
    mocks.uploadReplacementMediaVideo.mockImplementation((_file, _title, _profile, _key, _source, nextCallbacks) => {
      callbacks = nextCallbacks;
      return new Promise((resolve) => { finishUpload = resolve; });
    });
    window.history.replaceState({}, "", "/admin/media?media_id=media-ready&action=replace");
    render(<AdminMediaPage />);

    const dialog = await screen.findByRole("dialog", { name: "替换视频" });
    const profile = within(dialog).getByRole("combobox", { name: "替换视频转录方案" });
    await waitFor(() => expect(profile).toHaveValue(availableProfile.scheme_id));
    expect(within(dialog).getByRole("option", { name: /不可用方案/ })).toBeDisabled();
    const replacement = video("delivery-training-v2.mp4");
    fireEvent.change(within(dialog).getByLabelText("选择替换视频"), { target: { files: [replacement] } });
    fireEvent.change(profile, { target: { value: secondProfile.scheme_id } });
    fireEvent.click(within(dialog).getByRole("button", { name: "上传并开始转录" }));

    await waitFor(() => expect(mocks.uploadReplacementMediaVideo).toHaveBeenCalledWith(
      replacement,
      assets[0].title,
      secondProfile.scheme_id,
      expect.any(String),
      assets[0].media_id,
      expect.objectContaining({ onProgress: expect.any(Function), onUploaded: expect.any(Function) }),
    ));
    act(() => callbacks?.onProgress({ loaded: 42, total: 100, ratio: 0.42 }));
    expect(within(dialog).getByText("正在上传候选视频")).toBeInTheDocument();
    expect(within(dialog).getByText("42%")).toBeInTheDocument();
    expect(within(dialog).getByRole("progressbar", { name: "替换视频上传进度" })).toHaveAttribute("aria-valuenow", "42");

    act(() => callbacks?.onUploaded());
    expect(within(dialog).getByText("服务端正在准备转录任务")).toBeInTheDocument();
    expect(within(dialog).getByRole("progressbar", { name: "替换视频上传进度" })).not.toHaveAttribute("aria-valuenow");

    await act(async () => finishUpload?.({ ...assets[0], media_id: "media-replacement-candidate", transcription_job_id: "job-replacement" }));
    await waitFor(() => expect(mocks.getTranscriptionJob).toHaveBeenCalledWith("job-replacement"));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "替换视频" })).not.toBeInTheDocument());
    expect(window.location.search).toBe("");
  });

  it("shows filter counts and refreshes media and transcription state together", async () => {
    render(<AdminMediaPage />);
    await screen.findByText("项目交付培训");

    expect(screen.getByRole("button", { name: "全部任务 1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "待审核 1" })).toBeInTheDocument();
    const mediaLoads = mocks.listMediaAssets.mock.calls.length;
    const jobLoads = mocks.listTranscriptionJobs.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "刷新媒体资源" }));

    await waitFor(() => expect(mocks.listMediaAssets).toHaveBeenCalledTimes(mediaLoads + 1));
    await waitFor(() => expect(mocks.listTranscriptionJobs).toHaveBeenCalledTimes(jobLoads + 1));
    expect(screen.getByText(/最近刷新/)).toBeInTheDocument();
  });

  it("uses one transcription status when media and transcription both fail", async () => {
    const failedJob = {
      ...succeededJob,
      job_id: "job-media-failed",
      media_id: "media-transcription-failed",
      status: "failed" as const,
      failure_error_code: "provider_unavailable",
      error_summary: "转录服务暂不可用",
      failure: { code: "provider_unavailable", message: "转录服务暂不可用", retryable: true },
    };
    mocks.listMediaAssets.mockResolvedValue([{ ...assets[0], media_id: "media-transcription-failed", title: "转录失败视频", status: "failed" }]);
    mocks.listTranscriptionJobs.mockResolvedValue([failedJob]);
    render(<AdminMediaPage />);

    const row = await screen.findByTestId("media-record-row");
    expect(within(row).getByText("转录失败")).toBeInTheDocument();
    expect(within(row).queryByText("失败", { exact: true })).not.toBeInTheDocument();
    expect(within(row).getByText("转录服务暂不可用")).toBeInTheDocument();
  });

  it("opens the transcription workbench in a sheet and returns to the media list", async () => {
    render(<AdminMediaPage />);
    const open = await screen.findByRole("button", { name: "进入转写工作台" });
    fireEvent.click(open);

    const sheet = await screen.findByRole("dialog", { name: "项目交付培训" });
    expect(sheet).toBeInTheDocument();
    expect(await within(sheet).findByText("暂无可审阅转录版本。")).toBeInTheDocument();
    expect(mocks.listTranscriptVersions).toHaveBeenCalledWith("media-ready");

    fireEvent.click(within(sheet).getByRole("button", { name: "关闭转写工作台" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "项目交付培训" })).not.toBeInTheDocument());
    expect(open).toBeInTheDocument();
  });

  it("groups repeat submissions by filename without merging their records", async () => {
    mocks.listMediaAssets.mockResolvedValue([
      assets[0],
      { ...assets[0], media_id: "media-ready-repeat", title: "第二次提交", status: "failed" },
    ]);
    mocks.listTranscriptionJobs.mockResolvedValue([succeededJob]);
    render(<AdminMediaPage />);

    expect(await screen.findAllByText("同名记录 2 条")).toHaveLength(2);
    expect(screen.getByText("项目交付培训")).toBeInTheDocument();
    expect(screen.getByText("第二次提交")).toBeInTheDocument();
  });

  it("accepts multiple MP4 files and applies one ordered scheme to selected rows", async () => {
    render(<AdminMediaPage />);
    await addVideosAndOpenMode([video("one.mp4"), video("two.mp4")], "自动转录");

    expect(screen.getAllByRole("option", { name: /不可用方案/ }).every((option) => option.hasAttribute("disabled"))).toBe(true);
    fireEvent.change(screen.getByLabelText("批量转录方案"), { target: { value: secondProfile.scheme_id } });
    fireEvent.click(screen.getByRole("button", { name: "应用到已选择视频" }));
    expect(screen.getByLabelText("one.mp4 的转录方案")).toHaveValue(secondProfile.scheme_id);
    expect(screen.getByLabelText("two.mp4 的转录方案")).toHaveValue(secondProfile.scheme_id);

    fireEvent.click(screen.getByRole("button", { name: "上传并创建自动转录任务" }));
    await waitFor(() => expect(mocks.uploadAutomaticMediaVideo).toHaveBeenCalledTimes(2));
    expect(mocks.uploadAutomaticMediaVideo.mock.calls[0][2]).toBe(secondProfile.scheme_id);
    expect(mocks.uploadAutomaticMediaVideo.mock.calls[0][3]).not.toBe(mocks.uploadAutomaticMediaVideo.mock.calls[1][3]);
  });

  it("preflights same-name media and uploads the selected rename into the target directory", async () => {
    mocks.preflightMediaUpload.mockImplementation(async (body: { category_id: string; items: Array<{ client_id: string }> }) => ({
      category_id: body.category_id,
      entries: [{
        client_id: body.items[0].client_id,
        status: "conflict",
        suggested_title: "training (1)",
        suggested_filename: "training (1).mp4",
        conflicts: [{
          media_id: "existing-media", item_id: "existing-item", version_id: "existing-version",
          title: "training", original_filename: "training.mp4", title_matches: true, filename_matches: true,
        }],
      }],
    }));
    render(<AdminMediaPage />);
    await addVideosAndOpenMode([video("training.mp4")], "自动转录");

    fireEvent.click(screen.getByRole("button", { name: "上传并创建自动转录任务" }));
    expect(await screen.findByText("发现同名资料")).toBeInTheDocument();
    expect(mocks.uploadAutomaticMediaVideo).not.toHaveBeenCalled();

    fireEvent.change(screen.getByText("处理方式").closest("label")!.querySelector("select")!, { target: { value: "rename" } });
    fireEvent.click(screen.getByRole("button", { name: "按选择上传" }));

    await waitFor(() => expect(mocks.uploadAutomaticMediaVideo).toHaveBeenCalledTimes(1));
    expect(mocks.uploadAutomaticMediaVideo).toHaveBeenCalledWith(
      expect.any(File),
      "training (1)",
      availableProfile.scheme_id,
      expect.any(String),
      expect.any(Object),
      expect.objectContaining({ categoryId: "cat-05", originalFilename: "training (1).mp4" }),
    );
  });

  it("shows exact file transfer and then a separate server preparation state", async () => {
    let callbacks: { onProgress: (progress: { loaded: number; total: number; ratio: number }) => void; onUploaded: () => void } | undefined;
    let finishUpload: ((value: typeof assets[number] & { transcription_job_id: string }) => void) | undefined;
    mocks.uploadAutomaticMediaVideo.mockImplementation((_file, _title, _profile, _key, nextCallbacks) => {
      callbacks = nextCallbacks;
      return new Promise((resolve) => { finishUpload = resolve; });
    });
    render(<AdminMediaPage />);
    await addVideosAndOpenMode([video("progress.mp4")], "自动转录");

    fireEvent.click(screen.getByRole("button", { name: "上传并创建自动转录任务" }));
    await waitFor(() => expect(callbacks).toBeDefined());
    act(() => callbacks?.onProgress({ loaded: 50, total: 100, ratio: 0.5 }));

    expect(screen.getByText("正在上传 · 50%")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "progress.mp4 上传进度" })).toHaveAttribute("aria-valuenow", "50");
    expect(screen.getByRole("progressbar", { name: "批量文件传输进度" })).toHaveAttribute("aria-valuenow", "50");

    act(() => callbacks?.onUploaded());
    expect(screen.getByText("文件已上传，正在准备音轨并创建转录任务")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "progress.mp4 上传进度" })).not.toHaveAttribute("aria-valuenow");

    await act(async () => finishUpload?.({ ...assets[0], transcription_job_id: "job-progress" }));
    await waitFor(() => expect(screen.getByText("已提交")).toBeInTheDocument());
  });

  it("closes a completed batch without treating it as an unfinished draft", async () => {
    render(<AdminMediaPage />);
    await addVideosAndOpenMode([video("completed.mp4")], "自动转录");

    fireEvent.click(screen.getByRole("button", { name: "上传并创建自动转录任务" }));
    await waitFor(() => expect(screen.getByText("已提交")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "完成并关闭" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "完成并关闭" }));
    expect(screen.queryByText("暂时关闭上传流程？")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "上传视频与转写" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传视频" })).toBeInTheDocument();
  });

  it("offers an explicit preserve action when closing an unfinished batch", async () => {
    render(<AdminMediaPage />);
    await addVideosAndOpenMode([video("unfinished.mp4")], "自动转录");

    fireEvent.click(screen.getByRole("button", { name: "关闭" }));

    const prompt = await screen.findByRole("dialog", { name: "暂时关闭上传流程？" });
    expect(within(prompt).getByText(/可保留未提交的视频和填写内容供下次继续/)).toBeInTheDocument();
    fireEvent.click(within(prompt).getByRole("button", { name: "关闭并保留" }));
    expect(screen.queryByRole("dialog", { name: "上传视频与转写" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /继续上传/ })).toBeInTheDocument();
  });

  it("can close and discard an unfinished batch from the close prompt", async () => {
    render(<AdminMediaPage />);
    await addVideosAndOpenMode([video("close-and-discard.mp4")], "自动转录");

    fireEvent.click(screen.getByRole("button", { name: "关闭" }));

    const prompt = await screen.findByRole("dialog", { name: "暂时关闭上传流程？" });
    expect(within(prompt).getByRole("button", { name: "继续操作" })).toBeInTheDocument();
    expect(within(prompt).getByRole("button", { name: "关闭并保留" })).toBeInTheDocument();
    fireEvent.click(within(prompt).getByRole("button", { name: "关闭并放弃" }));
    expect(screen.queryByRole("dialog", { name: "上传视频与转写" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /继续上传/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传视频" })).toBeInTheDocument();
  });

  it("makes abandoning the local upload draft a visible destructive action", async () => {
    render(<AdminMediaPage />);
    await addVideosAndOpenMode([video("discard.mp4")], "自动转录");

    fireEvent.click(screen.getByRole("button", { name: "放弃本次上传" }));
    const prompt = await screen.findByRole("dialog", { name: "放弃本次上传？" });
    fireEvent.click(within(prompt).getByRole("button", { name: "放弃并清空" }));
    expect(screen.queryByRole("dialog", { name: "上传视频与转写" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传视频" })).toBeInTheDocument();
  });

  it("allows each automatic row to override the batch scheme", async () => {
    render(<AdminMediaPage />);
    await addVideosAndOpenMode([video("one.mp4"), video("two.mp4")], "自动转录");
    fireEvent.change(screen.getByLabelText("two.mp4 的转录方案"), { target: { value: secondProfile.scheme_id } });
    fireEvent.click(screen.getByRole("button", { name: "上传并创建自动转录任务" }));

    await waitFor(() => expect(mocks.uploadAutomaticMediaVideo).toHaveBeenCalledTimes(2));
    const calls = mocks.uploadAutomaticMediaVideo.mock.calls;
    expect(calls.find((call) => call[0].name === "one.mp4")?.[2]).toBe(availableProfile.scheme_id);
    expect(calls.find((call) => call[0].name === "two.mp4")?.[2]).toBe(secondProfile.scheme_id);
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
    fireEvent.click(screen.getByRole("button", { name: /继续上传/ }));
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
    mocks.listMediaAssets.mockResolvedValue([
      { ...assets[0], status: "transcribing", available_actions: ["cancel_transcription"] },
      { ...assets[0], media_id: "media-failed", title: "失败视频", status: "failed", available_actions: ["retry_transcription"] },
    ]);
    mocks.listTranscriptionJobs.mockResolvedValue([running, failed]);
    render(<AdminMediaPage />);
    await screen.findByText("项目交付培训");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(mocks.cancelTranscriptionJob).toHaveBeenCalledWith("job-succeeded"));
    await waitFor(() => expect(mocks.retryTranscription).toHaveBeenCalledWith("media-failed", expect.any(String)));
  });

  it("uses indeterminate progress instead of a false zero percent while the model runs", async () => {
    vi.spyOn(Date, "now").mockReturnValue(1_000_000);
    mocks.listTranscriptionJobs.mockResolvedValue([{
      ...succeededJob,
      status: "running",
      stage: "transcribing",
      processed_ms: 0,
      total_ms: 600_000,
      started_at: 900,
      finished_at: null,
    }]);
    render(<AdminMediaPage />);

    const row = await screen.findByTestId("media-record-row");
    expect(within(row).getByText(/模型整段处理中/)).toHaveTextContent("视频时长 10分0秒");
    expect(within(row).getByText(/模型整段处理中/)).toHaveTextContent("已耗时 1分40秒");
    expect(within(row).queryByText(/0%/)).not.toBeInTheDocument();
    expect(within(row).getByRole("progressbar", { name: "转录进度：转录中" })).not.toHaveAttribute("aria-valuenow");
  });

  it("does not present an unknown video duration as zero seconds", async () => {
    vi.spyOn(Date, "now").mockReturnValue(1_000_000);
    mocks.listTranscriptionJobs.mockResolvedValue([{
      ...succeededJob,
      status: "running",
      stage: "transcribing",
      processed_ms: 0,
      total_ms: 0,
      started_at: 900,
      finished_at: null,
    }]);
    render(<AdminMediaPage />);

    const row = await screen.findByTestId("media-record-row");
    expect(within(row).getByText(/模型整段处理中/)).toHaveTextContent("正在读取视频时长");
    expect(within(row).queryByText(/视频时长 0秒/)).not.toBeInTheDocument();
  });

  it("shows a determinate percentage only when a real checkpoint exists", async () => {
    mocks.listTranscriptionJobs.mockResolvedValue([{
      ...succeededJob,
      status: "running",
      stage: "transcribing",
      processed_ms: 300_000,
      total_ms: 600_000,
      finished_at: null,
    }]);
    render(<AdminMediaPage />);

    const row = await screen.findByTestId("media-record-row");
    expect(within(row).getByText(/50%/)).toHaveTextContent("5分0秒 / 10分0秒");
    expect(within(row).getByRole("progressbar", { name: "转录进度：转录中" })).toHaveAttribute("aria-valuenow", "50");
  });

  it("offers controlled cleanup for a failed managed task with terminal job history", async () => {
    mocks.listMediaAssets.mockResolvedValue([{ ...assets[0], media_id: "media-upload-failed", title: "上传失败视频", status: "failed", available_actions: ["retry_transcription", "delete_failed"], disabled_actions: {} }]);
    mocks.listTranscriptionJobs.mockResolvedValue([{ ...succeededJob, media_id: "media-upload-failed", status: "failed" }]);
    render(<AdminMediaPage />);

    const remove = await screen.findByRole("button", { name: "清理失败任务" });
    fireEvent.click(remove);
    expect(screen.getByRole("dialog", { name: "清理失败任务" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认清理" }));

    await waitFor(() => expect(mocks.deleteFailedMediaAsset).toHaveBeenCalledWith("media-upload-failed"));
    await waitFor(() => expect(screen.queryByText("上传失败视频")).not.toBeInTheDocument());
  });

  it("retries a shared-source reservation failure without a transcription job", async () => {
    mocks.listMediaAssets.mockResolvedValue([{
      ...assets[0],
      media_id: "media-shared-failed",
      title: "共享培训视频",
      status: "failed",
      storage_kind: "external",
      available_actions: ["retry_transcription", "delete_failed"],
      disabled_actions: {},
    }]);
    mocks.listTranscriptionJobs.mockResolvedValue([]);
    mocks.retryTranscription.mockResolvedValue({
      ...succeededJob,
      media_id: "media-shared-failed",
      status: "pending",
    });
    render(<AdminMediaPage embedded />);

    fireEvent.click(await screen.findByRole("button", { name: "重试" }));
    await waitFor(() => expect(mocks.retryTranscription).toHaveBeenCalledWith(
      "media-shared-failed",
      expect.any(String),
    ));
  });

  it("shows the backend retry reason for a shared-source reservation failure", async () => {
    mocks.listMediaAssets.mockResolvedValue([{
      ...assets[0],
      media_id: "media-shared-failed",
      title: "共享培训视频",
      status: "failed",
      storage_kind: "external",
      available_actions: ["retry_transcription", "delete_failed"],
      disabled_actions: {},
    }]);
    mocks.listTranscriptionJobs.mockResolvedValue([]);
    mocks.retryTranscription.mockRejectedValue(
      new Error("当前没有可用的转录方案，请先调整共享目录的默认转录方案。"),
    );
    render(<AdminMediaPage embedded />);

    fireEvent.click(await screen.findByRole("button", { name: "重试" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "当前没有可用的转录方案，请先调整共享目录的默认转录方案。",
    );
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
  });

  it("cleans a shared-source failure while explaining that the original is preserved", async () => {
    mocks.listMediaAssets.mockResolvedValue([{
      ...assets[0],
      media_id: "media-shared-failed",
      title: "共享培训视频",
      status: "failed",
      storage_kind: "external",
      available_actions: ["retry_transcription", "delete_failed"],
      disabled_actions: {},
    }]);
    mocks.listTranscriptionJobs.mockResolvedValue([]);
    mocks.deleteFailedMediaAsset.mockResolvedValue({ media_id: "media-shared-failed", cleanup_mode: "reset" });
    render(<AdminMediaPage embedded />);

    fireEvent.click(await screen.findByRole("button", { name: "清理失败任务" }));
    expect(screen.getByText(/不会删除共享目录原文件/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认清理" }));

    await waitFor(() => expect(mocks.deleteFailedMediaAsset).toHaveBeenCalledWith("media-shared-failed"));
  });

  it("finishes stale shared cache cleanup without presenting the active task as failed", async () => {
    const activeJob = {
      ...succeededJob,
      job_id: "job-shared-active",
      media_id: "media-shared-active",
      status: "running" as const,
      finished_at: null,
    };
    mocks.listMediaAssets.mockResolvedValue([{
      ...assets[0],
      media_id: "media-shared-active",
      title: "共享视频当前任务",
      status: "transcribing",
      storage_kind: "external",
      available_actions: ["cancel_transcription", "finalize_failed_cleanup"],
      disabled_actions: {},
    }]);
    mocks.listTranscriptionJobs.mockResolvedValue([activeJob]);
    mocks.deleteFailedMediaAsset.mockResolvedValue({
      media_id: "media-shared-active",
      cleanup_mode: "reset",
    });
    render(<AdminMediaPage embedded />);

    fireEvent.click(await screen.findByRole("button", { name: "完成缓存清理" }));

    const dialog = screen.getByRole("dialog", { name: "完成缓存清理" });
    expect(dialog).toHaveTextContent("上次清理遗留的暂存缓存");
    expect(dialog).toHaveTextContent("不会取消或修改当前转录任务");
    fireEvent.click(screen.getByRole("button", { name: "确认清理" }));
    await waitFor(() => expect(mocks.deleteFailedMediaAsset).toHaveBeenCalledWith(
      "media-shared-active",
    ));
  });

  it("lists an uploaded shared source with only a committed cleanup marker", async () => {
    mocks.listMediaAssets.mockResolvedValue([{
      ...assets[0],
      media_id: "media-shared-stale",
      title: "共享视频遗留缓存",
      status: "uploaded",
      storage_kind: "external",
      transcription_job_id: null,
      transcription_job_status: null,
      available_actions: ["finalize_failed_cleanup"],
      disabled_actions: {},
    }]);
    mocks.listTranscriptionJobs.mockResolvedValue([]);
    render(<AdminMediaPage embedded />);

    expect(await screen.findByText("共享视频遗留缓存")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "完成缓存清理" })).toBeEnabled();
  });

  it("uses the authoritative finalize action when the shared task failed again", async () => {
    mocks.listMediaAssets.mockResolvedValue([{
      ...assets[0],
      media_id: "media-shared-failed-again",
      title: "共享视频再次失败",
      status: "failed",
      storage_kind: "external",
      available_actions: ["retry_transcription", "finalize_failed_cleanup"],
      disabled_actions: {},
    }]);
    mocks.listTranscriptionJobs.mockResolvedValue([]);
    render(<AdminMediaPage embedded />);

    fireEvent.click(await screen.findByRole("button", { name: "完成缓存清理" }));

    const dialog = screen.getByRole("dialog", { name: "完成缓存清理" });
    expect(dialog).toHaveTextContent("上次清理遗留的暂存缓存");
    expect(dialog).not.toHaveTextContent("本地失败任务和派生缓存");
  });

  it("includes authoritative finalize actions in a mixed bulk cleanup", async () => {
    mocks.listMediaAssets.mockResolvedValue([
      {
        ...assets[0],
        media_id: "media-managed-failed",
        title: "受管失败视频",
        status: "failed",
        available_actions: ["delete_failed"],
        disabled_actions: {},
      },
      {
        ...assets[0],
        media_id: "media-shared-stale",
        title: "共享视频遗留缓存",
        status: "uploaded",
        storage_kind: "external",
        transcription_job_id: null,
        transcription_job_status: null,
        available_actions: ["finalize_failed_cleanup"],
        disabled_actions: {},
      },
    ]);
    mocks.listTranscriptionJobs.mockResolvedValue([]);
    render(<AdminMediaPage embedded />);

    fireEvent.click(await screen.findByRole("checkbox", { name: "选择当前页视频" }));
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "清理所选（2）" }));

    expect(screen.getByRole("dialog", { name: "清理或收尾 2 个对象" })).toBeInTheDocument();
  });

  it("uses backend bulk actions and retains itemized partial failures", async () => {
    const failedAssets = [
      { ...assets[0], media_id: "media-failed-1", title: "失败视频一", status: "failed", available_actions: ["retry_transcription", "delete_failed"], disabled_actions: {} },
      { ...assets[0], media_id: "media-failed-2", title: "失败视频二", status: "failed", available_actions: ["retry_transcription", "delete_failed"], disabled_actions: {} },
    ];
    mocks.listMediaAssets.mockResolvedValue(failedAssets);
    mocks.listTranscriptionJobs.mockResolvedValue([]);
    mocks.bulkRetryTranscriptions.mockResolvedValue({
      items: [
        { media_id: "media-failed-1", status: "succeeded", transcription_job_id: "job-new" },
        { media_id: "media-failed-2", status: "failed", message: "共享文件暂时不可读" },
      ],
      succeeded: 1,
      failed: 1,
    });
    render(<AdminMediaPage embedded />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "选择当前页视频" }));
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "重试所选（2）" }));

    await waitFor(() => expect(mocks.bulkRetryTranscriptions).toHaveBeenCalledWith(
      ["media-failed-1", "media-failed-2"],
      expect.any(String),
    ));
    expect(await screen.findByText("共享文件暂时不可读")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "清理所选（2）" }));
    expect(screen.getByRole("dialog", { name: "清理 2 个失败任务" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认批量清理" }));
    await waitFor(() => expect(mocks.bulkDeleteFailedMediaAssets).toHaveBeenCalledWith([
      "media-failed-1",
      "media-failed-2",
    ]));
  });

  it("uses the managed-content upload entry when embedded as transcription tasks", async () => {
    render(<AdminMediaPage embedded />);
    expect(await screen.findByRole("heading", { name: "转录任务" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /上传视频/ })).not.toBeInTheDocument();
  });

  it("does not list managed videos before a transcription job is created", async () => {
    mocks.listMediaAssets.mockResolvedValue([
      {
        ...assets[0],
        media_id: "media-awaiting-transcription",
        title: "待转录视频",
        status: "uploaded",
        transcription_job_id: null,
        transcription_job_status: null,
      },
      {
        ...assets[0],
        media_id: "media-with-job",
        title: "已进入转录任务",
        transcription_job_id: succeededJob.job_id,
        transcription_job_status: succeededJob.status,
      },
    ]);
    mocks.listTranscriptionJobs.mockResolvedValue([
      { ...succeededJob, media_id: "media-with-job" },
    ]);

    render(<AdminMediaPage embedded />);

    expect(await screen.findByText("已进入转录任务")).toBeInTheDocument();
    expect(screen.queryByText("待转录视频")).not.toBeInTheDocument();
    expect(screen.getByText(/当前显示 1 - 1 \/ 1 条记录/)).toBeInTheDocument();
  });

  it("shows the transcription-task empty state when the library only has pending videos", async () => {
    mocks.listMediaAssets.mockResolvedValue([{
      ...assets[0],
      media_id: "media-awaiting-transcription",
      title: "待转录视频",
      status: "uploaded",
      transcription_job_id: null,
      transcription_job_status: null,
    }]);
    mocks.listTranscriptionJobs.mockResolvedValue([]);

    render(<AdminMediaPage embedded />);

    expect(await screen.findByText("暂无转录任务")).toBeInTheDocument();
    expect(screen.getByText(/当前显示 0 - 0 \/ 0 条记录/)).toBeInTheDocument();
  });

  it("keeps a permanent transcription failure retry button disabled from server capabilities", async () => {
    const failed = {
      ...succeededJob,
      status: "failed" as const,
      failure: { code: "provider_unavailable", message: "服务暂时不可用", retryable: true, recommended_action: "稍后重试" },
    };
    mocks.listMediaAssets.mockResolvedValue([{
      ...assets[0],
      status: "failed",
      available_actions: [],
      disabled_actions: { retry_transcription: "仅可重试失败或已取消且允许恢复的转录任务" },
    }]);
    mocks.listTranscriptionJobs.mockResolvedValue([failed]);
    render(<AdminMediaPage embedded />);

    const retry = await screen.findByRole("button", { name: "重试" });
    expect(retry).toBeDisabled();
    expect(retry).toHaveAttribute("title", "仅可重试失败或已取消且允许恢复的转录任务");
    fireEvent.click(retry);
    expect(mocks.retryTranscription).not.toHaveBeenCalled();
  });
});
