import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TranscriptionVersionPanel } from "./TranscriptionVersionPanel";

const mocks = vi.hoisted(() => ({
  listTranscriptVersions: vi.fn(),
  previewTranscriptVersion: vi.fn(),
  previewTranscriptVersionTimeline: vi.fn(),
  createTranscriptRevision: vi.fn(),
  reviewTranscriptVersion: vi.fn(),
  publishTranscriptVersion: vi.fn(),
  getTranscriptPublicationJob: vi.fn(),
  bulkDeleteTranscriptVersions: vi.fn(),
}));
vi.mock("../api/client", () => ({ api: mocks }));

const awaitingVersion = {
  version_id: "11111111-1111-4111-8111-111111111111",
  media_id: "media-1",
  source: "automatic",
  profile_id: "profile-1",
  provider_key: "remote-asr",
  model_id: "model-1",
  model_revision: "commit-abc",
  markdown_storage_kind: "managed_artifact",
  review_status: "awaiting_review",
  reviewed_by: null,
  reviewed_at: null,
  review_note: null,
  publication_status: "not_published",
  published_at: null,
  supersedes_version_id: null,
  derived_from_version_id: null,
  edited_by: null,
  markdown_sha256: "a".repeat(64),
  created_at: 1,
  updated_at: 1,
  is_current: false,
};

const approvedVersion = { ...awaitingVersion, review_status: "review_approved" as const, reviewed_by: 1, reviewed_at: 2 };
const revisedVersion = {
  ...awaitingVersion,
  version_id: "22222222-2222-4222-8222-222222222222",
  source: "manual",
  profile_id: null,
  provider_key: null,
  model_id: null,
  model_revision: null,
  derived_from_version_id: awaitingVersion.version_id,
  edited_by: 1,
  markdown_sha256: "b".repeat(64),
};

// Deletable: a never-published, reviewed historical version that nothing references.
const deletableVersion = {
  ...awaitingVersion,
  version_id: "55555555-5555-4555-8555-555555555555",
  source: "manual",
  profile_id: null,
  provider_key: null,
  model_id: null,
  model_revision: null,
  derived_from_version_id: null,
  supersedes_version_id: null,
  review_status: "review_approved" as const,
  reviewed_by: 1,
  reviewed_at: 2,
  markdown_sha256: "c".repeat(64),
};
const publishedVersion = {
  ...approvedVersion,
  version_id: "33333333-3333-4333-8333-333333333333",
  derived_from_version_id: null,
  publication_status: "published" as const,
  published_at: 3,
};
const currentHeadVersion = {
  ...approvedVersion,
  version_id: "44444444-4444-4444-8444-444444444444",
  derived_from_version_id: null,
  publication_status: "published" as const,
  published_at: 3,
  is_current: true,
};

describe("TranscriptionVersionPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listTranscriptVersions.mockResolvedValue([awaitingVersion]);
    mocks.previewTranscriptVersion.mockResolvedValue({ version_id: awaitingVersion.version_id, markdown: "说话人 1 00:00:00\n**培训开始**\n", markdown_sha256: awaitingVersion.markdown_sha256 });
    mocks.previewTranscriptVersionTimeline.mockResolvedValue({
      media_id: "media-1",
      version_id: awaitingVersion.version_id,
      language: "zh-CN",
      duration_ms: 7000,
      segments: [{ id: 0, start_ms: 0, end_ms: null, text: "培训开始" }],
    });
    mocks.createTranscriptRevision.mockResolvedValue(revisedVersion);
    mocks.reviewTranscriptVersion.mockResolvedValue(approvedVersion);
    mocks.publishTranscriptVersion.mockResolvedValue({ version: { ...approvedVersion, publication_status: "publishing" }, job: null, reused: false });
  });

  it("loads lazily and renders the Markdown preview", async () => {
    render(<TranscriptionVersionPanel mediaId="media-1" />);
    expect(mocks.listTranscriptVersions).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "审阅转录版本" }));
    expect(await screen.findByRole("button", { name: "校对内容" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "校对内容" }));
    expect(await screen.findByRole("textbox", { name: "转录 Markdown 编辑器" })).toBeInTheDocument();
    const rendered = await screen.findByText("培训开始", { selector: "strong" });
    expect(rendered).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "视频转录稿" })).toBeInTheDocument();
    expect(screen.getByLabelText("视频播放器")).toHaveAttribute(
      "src",
      "/api/admin/media/media-1/preview",
    );
  });

  it("shows the transcription scheme used by each version", async () => {
    const withScheme = {
      ...awaitingVersion,
      scheme_id: "whisperx-balanced-v2",
      scheme_name: "WhisperX 均衡分段",
      scheme_deleted: false,
    };
    mocks.listTranscriptVersions.mockResolvedValue([withScheme]);
    render(<TranscriptionVersionPanel mediaId="media-1" embedded />);

    expect(await screen.findByTestId("version-scheme-line")).toHaveTextContent(
      "转录方案：WhisperX 均衡分段",
    );
    expect(screen.queryByText("原转录配置已删除")).not.toBeInTheDocument();
  });

  it("marks a removed custom scheme as deleted on the version", async () => {
    const withRemovedScheme = {
      ...awaitingVersion,
      scheme_id: "custom-removed",
      scheme_name: "自定义强校方案",
      scheme_deleted: true,
    };
    mocks.listTranscriptVersions.mockResolvedValue([withRemovedScheme]);
    render(<TranscriptionVersionPanel mediaId="media-1" embedded />);

    expect(await screen.findByTestId("version-scheme-line")).toHaveTextContent(
      "转录方案：自定义强校方案",
    );
    expect(screen.getByText("原转录配置已删除")).toBeInTheDocument();
  });

  it("keeps multiple versions in a compact navigator and opens one workspace", async () => {
    const second = { ...revisedVersion, version_id: "33333333-3333-4333-8333-333333333333" };
    const third = { ...approvedVersion, version_id: "44444444-4444-4444-8444-444444444444" };
    mocks.listTranscriptVersions.mockResolvedValue([awaitingVersion, second, third]);
    render(<TranscriptionVersionPanel mediaId="media-1" embedded />);

    expect(await screen.findByText("3 个版本")).toBeInTheDocument();
    const openButtons = screen.getAllByRole("button", { name: "校对内容" });
    expect(openButtons).toHaveLength(3);
    fireEvent.click(openButtons[0]);
    expect(await screen.findByRole("region", { name: "当前版本校对工作区" })).toBeInTheDocument();
    expect(screen.getAllByRole("region", { name: "当前版本校对工作区" })).toHaveLength(1);

    fireEvent.click(screen.getAllByRole("button", { name: "校对内容" })[0]);
    await waitFor(() => expect(mocks.previewTranscriptVersion).toHaveBeenCalledWith(second.version_id));
    expect(screen.getAllByRole("region", { name: "当前版本校对工作区" })).toHaveLength(1);
  });

  it("opens the current published version directly for an edit-current deep link", async () => {
    const historical = { ...awaitingVersion, version_id: "33333333-3333-4333-8333-333333333333" };
    const current = { ...approvedVersion, version_id: "44444444-4444-4444-8444-444444444444", is_current: true };
    mocks.listTranscriptVersions.mockResolvedValue([historical, current]);
    mocks.previewTranscriptVersion.mockResolvedValue({
      version_id: current.version_id,
      markdown: "说话人 1 00:00:00\n当前正式稿\n",
      markdown_sha256: current.markdown_sha256,
    });
    render(<TranscriptionVersionPanel mediaId="media-1" embedded initialAction="edit-current" />);

    expect(await screen.findByRole("region", { name: "当前版本校对工作区" })).toBeInTheDocument();
    await waitFor(() => expect(mocks.previewTranscriptVersion).toHaveBeenCalledWith(current.version_id));
    expect(screen.getByRole("textbox", { name: "转录 Markdown 编辑器" })).toHaveValue("说话人 1 00:00:00\n当前正式稿\n");
  });

  it("saves edits as a new draft and refreshes the selected version", async () => {
    mocks.listTranscriptVersions
      .mockResolvedValueOnce([awaitingVersion])
      .mockResolvedValue([revisedVersion, awaitingVersion]);
    render(<TranscriptionVersionPanel mediaId="media-1" embedded />);
    fireEvent.click(await screen.findByRole("button", { name: "校对内容" }));
    fireEvent.change(await screen.findByRole("textbox", { name: "转录 Markdown 编辑器" }), {
      target: { value: "说话人 1 00:00:00\n校对后的内容\n" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存为新草稿" }));

    await waitFor(() => expect(mocks.createTranscriptRevision).toHaveBeenCalledWith(
      awaitingVersion.version_id,
      "说话人 1 00:00:00\n校对后的内容\n",
      awaitingVersion.markdown_sha256,
      expect.stringMatching(/^[0-9a-f-]{36}$/),
    ));
    expect(await screen.findByText("新草稿已保存，审核状态已重置为待审核。")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "转录 Markdown 编辑器" })).toHaveValue("说话人 1 00:00:00\n校对后的内容\n");
  });

  it("submits review note and keeps publish disabled before approval", async () => {
    render(<TranscriptionVersionPanel mediaId="media-1" />);
    fireEvent.click(screen.getByRole("button", { name: "审阅转录版本" }));
    await screen.findByRole("textbox", { name: `审核备注 ${awaitingVersion.version_id}` });
    expect(screen.getByRole("button", { name: "发布到知识库" })).toBeDisabled();
    expect(screen.getByText("审核通过后可发布")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(`审核备注 ${awaitingVersion.version_id}`), { target: { value: "已校对" } });
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    await waitFor(() => expect(mocks.reviewTranscriptVersion).toHaveBeenCalledWith(awaitingVersion.version_id, true, "已校对"));
  });

  it("rejects a version with the immutable review note", async () => {
    render(<TranscriptionVersionPanel mediaId="media-1" />);
    fireEvent.click(screen.getByRole("button", { name: "审阅转录版本" }));
    await screen.findByRole("textbox", { name: `审核备注 ${awaitingVersion.version_id}` });
    fireEvent.change(screen.getByLabelText(`审核备注 ${awaitingVersion.version_id}`), { target: { value: "时间轴需修正" } });
    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));
    await waitFor(() => expect(mocks.reviewTranscriptVersion).toHaveBeenCalledWith(awaitingVersion.version_id, false, "时间轴需修正"));
  });

  it("keeps publication disabled for an approved manual version", async () => {
    mocks.listTranscriptVersions.mockResolvedValue([{ ...approvedVersion, source: "manual", markdown_storage_kind: "legacy_manual", profile_id: null, provider_key: null, model_id: null, model_revision: null }]);
    render(<TranscriptionVersionPanel mediaId="media-1" />);
    fireEvent.click(screen.getByRole("button", { name: "审阅转录版本" }));
    expect(await screen.findByRole("button", { name: "发布到知识库" })).toBeDisabled();
  });

  it("publishes only an approved version", async () => {
    mocks.listTranscriptVersions.mockResolvedValue([approvedVersion]);
    render(<TranscriptionVersionPanel mediaId="media-1" />);
    fireEvent.click(screen.getByRole("button", { name: "审阅转录版本" }));
    const publish = await screen.findByRole("button", { name: "发布到知识库" });
    expect(publish).toBeEnabled();
    fireEvent.click(publish);
    await waitFor(() => expect(mocks.publishTranscriptVersion).toHaveBeenCalledWith(approvedVersion.version_id));
  });

  it("allows an approved managed manual revision to publish", async () => {
    const approvedRevision = { ...revisedVersion, review_status: "review_approved" as const, reviewed_by: 1, reviewed_at: 3 };
    mocks.listTranscriptVersions.mockResolvedValue([approvedRevision]);
    render(<TranscriptionVersionPanel mediaId="media-1" embedded />);
    const publish = await screen.findByRole("button", { name: "发布到知识库" });
    expect(screen.getByText("人工修订")).toBeInTheDocument();
    expect(publish).toBeEnabled();
    fireEvent.click(publish);
    await waitFor(() => expect(mocks.publishTranscriptVersion).toHaveBeenCalledWith(approvedRevision.version_id));
  });

  it("does not switch versions when unsaved changes are not discarded", async () => {
    const second = { ...awaitingVersion, version_id: "33333333-3333-4333-8333-333333333333" };
    mocks.listTranscriptVersions.mockResolvedValue([awaitingVersion, second]);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<TranscriptionVersionPanel mediaId="media-1" embedded />);
    const buttons = await screen.findAllByRole("button", { name: "校对内容" });
    fireEvent.click(buttons[0]);
    fireEvent.change(await screen.findByRole("textbox", { name: "转录 Markdown 编辑器" }), {
      target: { value: "说话人 1 00:00:00\n未保存修改\n" },
    });
    fireEvent.click(buttons[1]);

    expect(confirm).toHaveBeenCalled();
    expect(mocks.previewTranscriptVersion).toHaveBeenCalledTimes(1);
    confirm.mockRestore();
  });

  it("notifies the parent after a review changes the media lifecycle", async () => {
    const onChanged = vi.fn();
    render(<TranscriptionVersionPanel mediaId="media-1" embedded onChanged={onChanged} />);
    await screen.findByRole("button", { name: "审核通过" });
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
  });

  it("keeps the version list in a left rail column that can scroll on its own", async () => {
    mocks.listTranscriptVersions.mockResolvedValue([deletableVersion, revisedVersion]);
    const { container } = render(<TranscriptionVersionPanel mediaId="media-1" embedded />);

    await screen.findByText("2 个版本");
    const grid = container.querySelector(".grid");
    expect(grid?.className).toContain("lg:grid-cols-[minmax(14rem,18rem)_minmax(0,1fr)]");
    const details = screen.getByTestId("version-list-region");
    expect(details.className).toContain("lg:contents");
    expect(details).not.toHaveAttribute("open");
    const list = screen.getByRole("region", { name: "转录版本列表" });
    expect(list.className).toContain("lg:sticky");
    expect(list.className).toContain("lg:max-h-[calc(100vh-12rem)]");
    expect(list.className).toContain("lg:overflow-hidden");
  });

  it("shows the loading placeholder before the version list resolves", async () => {
    let resolveVersions: (versions: unknown[]) => void = () => {};
    mocks.listTranscriptVersions.mockReturnValue(new Promise((resolve) => { resolveVersions = resolve; }));
    render(<TranscriptionVersionPanel mediaId="media-1" embedded />);

    expect(await screen.findByText("正在加载转录版本…")).toBeInTheDocument();
    expect(screen.getByText("转录版本 · 共 0 个")).toBeInTheDocument();

    await act(async () => {
      resolveVersions([deletableVersion, revisedVersion]);
    });
    expect(await screen.findByText("2 个版本")).toBeInTheDocument();
    expect(screen.queryByText("正在加载转录版本…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批量选择" })).toBeEnabled();
  });

  it("explains an empty version list", async () => {
    mocks.listTranscriptVersions.mockResolvedValue([]);
    render(<TranscriptionVersionPanel mediaId="media-1" embedded />);

    expect(await screen.findByText("暂无可审阅转录版本。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批量选择" })).toBeDisabled();
    expect(screen.queryByRole("region", { name: "当前版本校对工作区" })).not.toBeInTheDocument();
  });

  it("reports a version list failure and keeps the retry path usable", async () => {
    mocks.listTranscriptVersions.mockRejectedValue(new Error("版本列表暂时不可用"));
    render(<TranscriptionVersionPanel mediaId="media-1" embedded />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("转录版本操作失败");
    expect(alert).toHaveTextContent("版本列表暂时不可用");
    expect(screen.getByRole("button", { name: "批量选择" })).toBeDisabled();
  });

  it("pages to the neighbouring version from the left rail buttons", async () => {
    mocks.listTranscriptVersions.mockResolvedValue([deletableVersion, revisedVersion, publishedVersion]);
    render(<TranscriptionVersionPanel mediaId="media-1" embedded />);

    const previous = await screen.findByRole("button", { name: "上一版" });
    const next = screen.getByRole("button", { name: "下一版" });
    expect(previous).toBeDisabled();
    expect(next).toBeDisabled();

    fireEvent.click(screen.getAllByRole("button", { name: "校对内容" })[1]);
    await waitFor(() => expect(mocks.previewTranscriptVersion).toHaveBeenCalledWith(revisedVersion.version_id));
    await waitFor(() => expect(previous).toBeEnabled());
    expect(next).toBeEnabled();

    fireEvent.click(next);
    await waitFor(() => expect(mocks.previewTranscriptVersion).toHaveBeenCalledWith(publishedVersion.version_id));
    await waitFor(() => expect(next).toBeDisabled());

    fireEvent.click(previous);
    await waitFor(() => expect(mocks.previewTranscriptVersion).toHaveBeenCalledWith(revisedVersion.version_id));
    expect(mocks.previewTranscriptVersion).toHaveBeenCalledTimes(3);
  });

  it("pages with Alt+ArrowUp and Alt+ArrowDown and loads that preview", async () => {
    mocks.listTranscriptVersions.mockResolvedValue([deletableVersion, revisedVersion, publishedVersion]);
    render(<TranscriptionVersionPanel mediaId="media-1" embedded />);

    fireEvent.click((await screen.findAllByRole("button", { name: "校对内容" }))[1]);
    await waitFor(() => expect(mocks.previewTranscriptVersion).toHaveBeenCalledWith(revisedVersion.version_id));

    fireEvent.keyDown(document.body, { key: "ArrowDown", altKey: true });
    await waitFor(() => expect(mocks.previewTranscriptVersion).toHaveBeenCalledWith(publishedVersion.version_id));

    fireEvent.keyDown(document.body, { key: "ArrowUp", altKey: true });
    await waitFor(() => expect(mocks.previewTranscriptVersion).toHaveBeenCalledWith(revisedVersion.version_id));
    expect(mocks.previewTranscriptVersion).toHaveBeenCalledTimes(3);
  });

  it("surfaces a failed paging preview without losing the list", async () => {
    mocks.listTranscriptVersions.mockResolvedValue([deletableVersion, revisedVersion]);
    mocks.previewTranscriptVersion.mockRejectedValue(new Error("版本预览暂时不可用"));
    render(<TranscriptionVersionPanel mediaId="media-1" embedded />);

    fireEvent.click((await screen.findAllByRole("button", { name: "校对内容" }))[0]);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("版本预览暂时不可用");
    expect(screen.getByText("2 个版本")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "当前版本校对工作区" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "校对内容" })).toHaveLength(2);
  });

  it("enables batch selection and disables versions that must be kept", async () => {
    mocks.listTranscriptVersions.mockResolvedValue([awaitingVersion, deletableVersion, revisedVersion, publishedVersion, currentHeadVersion]);
    render(<TranscriptionVersionPanel mediaId="media-1" embedded />);

    fireEvent.click(await screen.findByRole("button", { name: "批量选择" }));

    // 版本 1 awaits review and is referenced by 版本 3; 版本 2 is a plain deletable draft.
    expect(await screen.findByRole("checkbox", { name: "选择版本 1" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "选择版本 2" })).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: "选择版本 3" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "选择版本 4" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "选择版本 5" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "全选可删除版本" })).toBeEnabled();
    expect(screen.getByText("已选 0 个")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除所选" })).toBeDisabled();
    expect(screen.getByText("不能删除：正在等待审核，不能删除")).toBeInTheDocument();
    expect(screen.getByText("不能删除：已发布到知识库，不能删除")).toBeInTheDocument();
    expect(screen.getByText("不能删除：当前正式检索版本，不能删除")).toBeInTheDocument();
    expect(screen.getByText("不能删除：已被其他版本引用，不能删除")).toBeInTheDocument();
  });

  it("keeps awaiting-review versions out of bulk selection with an explanation", async () => {
    mocks.listTranscriptVersions.mockResolvedValue([awaitingVersion, deletableVersion]);
    render(<TranscriptionVersionPanel mediaId="media-1" embedded />);

    fireEvent.click(await screen.findByRole("button", { name: "批量选择" }));

    expect(screen.getByRole("checkbox", { name: "选择版本 1" })).toBeDisabled();
    expect(screen.getByText("不能删除：正在等待审核，不能删除")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "选择版本 2" })).toBeEnabled();
  });

  it("keeps legacy hand-written transcripts out of the delete flow", async () => {
    const legacyManual = { ...deletableVersion, version_id: "66666666-6666-4666-8666-666666666666", markdown_storage_kind: "legacy_manual" };
    mocks.listTranscriptVersions.mockResolvedValue([legacyManual]);
    render(<TranscriptionVersionPanel mediaId="media-1" embedded />);

    fireEvent.click(await screen.findByRole("button", { name: "批量选择" }));

    expect(screen.getByRole("checkbox", { name: "选择版本 1" })).toBeDisabled();
    expect(screen.getByText("不能删除：早期人工转录稿由系统归档，不能删除")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "全选可删除版本" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "删除所选" })).toBeDisabled();
  });

  it("confirms deletion of only the selected versions and renders partial success", async () => {
    mocks.listTranscriptVersions
      .mockResolvedValueOnce([deletableVersion, revisedVersion, publishedVersion, currentHeadVersion])
      .mockResolvedValue([publishedVersion, currentHeadVersion]);
    mocks.bulkDeleteTranscriptVersions.mockResolvedValue({
      items: [
        { version_id: deletableVersion.version_id, status: "deleted", reason: null },
        { version_id: revisedVersion.version_id, status: "deleted", reason: null },
        { version_id: publishedVersion.version_id, status: "unavailable", reason: "已发布到知识库，不能删除" },
        { version_id: currentHeadVersion.version_id, status: "conflict", reason: "当前正式检索版本，不能删除" },
      ],
      deleted_count: 2,
      skipped_count: 2,
    });
    const onChanged = vi.fn();
    render(<TranscriptionVersionPanel mediaId="media-1" embedded onChanged={onChanged} />);

    fireEvent.click(await screen.findByRole("button", { name: "批量选择" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "选择版本 1" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "选择版本 2" }));
    expect(screen.getByText("已选 2 个")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "选择版本 3" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "删除所选" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("删除后无法恢复");
    expect(dialog).toHaveTextContent("版本 1 · 人工转录");
    expect(dialog).toHaveTextContent("版本 2 · 人工修订");
    expect(dialog).toHaveTextContent("转写正文、时间轴及系统保存的转写产物文件都会被永久删除");
    expect(dialog).not.toHaveTextContent("版本 3");

    fireEvent.click(within(dialog).getByRole("button", { name: "确认删除 2 个版本" }));

    await waitFor(() => expect(mocks.bulkDeleteTranscriptVersions).toHaveBeenCalledWith(
      "media-1",
      [deletableVersion.version_id, revisedVersion.version_id],
      expect.stringMatching(/^[0-9a-f-]{36}$/),
    ));

    const result = await screen.findByTestId("version-bulk-delete-result");
    expect(result).toHaveTextContent("成功 2 · 跳过 2");
    expect(result).toHaveTextContent("版本 3：已发布到知识库，不能删除");
    expect(result).toHaveTextContent("版本 4：当前正式检索版本，不能删除");

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    await waitFor(() => expect(mocks.listTranscriptVersions).toHaveBeenCalledTimes(2));
    expect(screen.getByText("2 个版本")).toBeInTheDocument();
  });

  it("keeps the dialog open, reports the failure and reuses one idempotency key on retry", async () => {
    mocks.listTranscriptVersions.mockResolvedValue([deletableVersion]);
    let rejectRun: (error: Error) => void = () => {};
    const pending = new Promise((_resolve, reject) => { rejectRun = reject; });
    mocks.bulkDeleteTranscriptVersions.mockReturnValueOnce(pending);
    render(<TranscriptionVersionPanel mediaId="media-1" embedded />);

    fireEvent.click(await screen.findByRole("button", { name: "批量选择" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "选择版本 1" }));
    fireEvent.click(screen.getByRole("button", { name: "删除所选" }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "确认删除 1 个版本" }));

    expect(await within(dialog).findByText("正在删除…")).toBeInTheDocument();

    rejectRun(new Error("批量删除暂时不可用"));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("批量删除暂时不可用");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("已选 1 个")).toBeInTheDocument();

    const firstKey = mocks.bulkDeleteTranscriptVersions.mock.calls[0][2];
    mocks.bulkDeleteTranscriptVersions.mockResolvedValueOnce({
      items: [{ version_id: deletableVersion.version_id, status: "deleted", reason: null }],
      deleted_count: 1,
      skipped_count: 0,
    });
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "确认删除 1 个版本" }));

    await waitFor(() => expect(mocks.bulkDeleteTranscriptVersions).toHaveBeenCalledTimes(2));
    expect(mocks.bulkDeleteTranscriptVersions.mock.calls[1][2]).toBe(firstKey);
  });
});

