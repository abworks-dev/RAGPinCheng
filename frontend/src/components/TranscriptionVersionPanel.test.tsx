import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TranscriptionVersionPanel } from "./TranscriptionVersionPanel";

const mocks = vi.hoisted(() => ({
  listTranscriptVersions: vi.fn(),
  previewTranscriptVersion: vi.fn(),
  createTranscriptRevision: vi.fn(),
  reviewTranscriptVersion: vi.fn(),
  publishTranscriptVersion: vi.fn(),
  getTranscriptPublicationJob: vi.fn(),
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

describe("TranscriptionVersionPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listTranscriptVersions.mockResolvedValue([awaitingVersion]);
    mocks.previewTranscriptVersion.mockResolvedValue({ version_id: awaitingVersion.version_id, markdown: "说话人 1 00:00:00\n**培训开始**\n", markdown_sha256: awaitingVersion.markdown_sha256 });
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
});

