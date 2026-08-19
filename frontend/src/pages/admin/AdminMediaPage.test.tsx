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
  });

  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", "/admin/media");
    let sequence = 0;
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn(() => `11111111-1111-4111-8111-${String(++sequence).padStart(12, "0")}`),
    });
    mocks.listMediaAssets.mockResolvedValue(assets);
    mocks.deleteFailedMediaAsset.mockResolvedValue(undefined);
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

  it("keeps external media disabled when the server has no configured root aliases", async () => {
    render(<AdminMediaPage />);
    expect(await screen.findByText("未配置共享目录根")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /登记共享目录/ })).toBeDisabled();
  });

  it("shows a recoverable error when shared media cannot be loaded", async () => {
    mocks.listExternalMediaRoots.mockRejectedValue(new Error("共享资料源服务不可用"));
    render(<AdminMediaPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("共享资料源服务不可用");
    expect(screen.getByRole("button", { name: "刷新" })).toBeEnabled();
  });

  it("browses a shared folder and explicitly enqueues selected videos", async () => {
    mocks.listExternalMediaRoots.mockResolvedValue([{ alias: "training-share" }]);
    mocks.listExternalMediaSources.mockResolvedValue([{
      id: "source-1", name: "培训归档", root_alias: "training-share", relative_path: "2026",
      target_category_id: "cat-05", default_scheme_id: availableProfile.scheme_id,
      auto_enqueue: false, scan_interval_seconds: 900, enabled: true, status: "available",
      total_files: 2, available_files: 2, missing_files: 0, last_scan_at: 100,
      last_successful_scan_at: 100, last_error_code: null, created_at: 1, updated_at: 100, version: 2,
    }]);
    mocks.listExternalMediaEntries.mockResolvedValue({
      source_id: "source-1", parent_relative_path: "", entries: [
        { id: "folder:课程", kind: "folder", name: "课程", relative_path: "课程" },
        { id: "entry-1", kind: "video", name: "intro.mp4", relative_path: "intro.mp4", file_size: 1024, availability: "available", media_id: "external-media-1", media_status: "uploaded", transcription_job_id: null, transcription_job_status: null, review_status: null, publication_status: null, index_status: null },
      ],
    });
    render(<AdminMediaPage />);
    expect((await screen.findAllByText("培训归档")).length).toBeGreaterThan(0);
    fireEvent.click(await screen.findByLabelText("选择 intro.mp4"));
    fireEvent.click(screen.getByRole("button", { name: "加入转录（1）" }));
    await waitFor(() => expect(mocks.enqueueExternalMedia).toHaveBeenCalledWith("source-1", ["entry-1"]));
    expect(await screen.findByText("已加入 1 个转录任务。" )).toBeInTheDocument();
  });

  it("requires confirmation before enqueueing all pending shared videos", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    mocks.listExternalMediaRoots.mockResolvedValue([{ alias: "training-share" }]);
    mocks.listExternalMediaSources.mockResolvedValue([{
      id: "source-1", name: "培训归档", root_alias: "training-share", relative_path: "",
      target_category_id: "cat-05", default_scheme_id: availableProfile.scheme_id,
      auto_enqueue: false, scan_interval_seconds: 900, enabled: true, status: "available",
      total_files: 501, available_files: 501, missing_files: 0, last_scan_at: 100,
      last_successful_scan_at: 100, last_error_code: null, created_at: 1, updated_at: 100, version: 2,
    }]);
    mocks.listExternalMediaEntries.mockResolvedValue({ source_id: "source-1", parent_relative_path: "", entries: [] });
    mocks.enqueueExternalMedia.mockResolvedValue({ requested: 500, enqueued: 500, failed: 0, failures: {} });
    render(<AdminMediaPage />);

    const enqueueAll = await screen.findByRole("button", { name: "全部待转录视频" });
    fireEvent.click(enqueueAll);
    expect(mocks.enqueueExternalMedia).not.toHaveBeenCalled();
    fireEvent.click(enqueueAll);
    await waitFor(() => expect(mocks.enqueueExternalMedia).toHaveBeenCalledWith("source-1"));
    expect(await screen.findByText(/如仍有待处理视频，可再次执行/)).toBeInTheDocument();
    confirm.mockRestore();
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

    expect(screen.getByRole("button", { name: "全部 1" })).toHaveAttribute("aria-pressed", "true");
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
    mocks.listMediaAssets.mockResolvedValue([assets[0], { ...assets[0], media_id: "media-failed", title: "失败视频" }]);
    mocks.listTranscriptionJobs.mockResolvedValue([running, failed]);
    render(<AdminMediaPage />);
    await screen.findByText("项目交付培训");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(mocks.cancelTranscriptionJob).toHaveBeenCalledWith("job-succeeded"));
    await waitFor(() => expect(mocks.retryTranscription).toHaveBeenCalledWith("media-failed", availableProfile.scheme_id, expect.any(String)));
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

  it("offers complete deletion only for a failed upload without a transcription job", async () => {
    mocks.listMediaAssets.mockResolvedValue([{ ...assets[0], media_id: "media-upload-failed", title: "上传失败视频", status: "failed" }]);
    mocks.listTranscriptionJobs.mockResolvedValue([]);
    render(<AdminMediaPage />);

    const remove = await screen.findByRole("button", { name: "完整删除" });
    fireEvent.click(remove);
    expect(screen.getByRole("dialog", { name: "完整删除失败视频" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "完整删除" }));

    await waitFor(() => expect(mocks.deleteFailedMediaAsset).toHaveBeenCalledWith("media-upload-failed"));
    await waitFor(() => expect(screen.queryByText("上传失败视频")).not.toBeInTheDocument());
  });

  it("uses the managed-content upload entry when embedded as transcription tasks", async () => {
    render(<AdminMediaPage embedded />);
    expect(await screen.findByRole("heading", { name: "转录任务" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /上传视频/ })).not.toBeInTheDocument();
  });

  it("keeps a permanent transcription failure retry button disabled from server capabilities", async () => {
    const failed = {
      ...succeededJob,
      status: "failed" as const,
      failure: { code: "invalid_media", message: "视频不可解析", retryable: false, recommended_action: "更换视频" },
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
