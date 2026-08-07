import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TranscriptionVersionPanel } from "./TranscriptionVersionPanel";

const mocks = vi.hoisted(() => ({
  listTranscriptVersions: vi.fn(),
  previewTranscriptVersion: vi.fn(),
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
  review_status: "awaiting_review",
  reviewed_by: null,
  reviewed_at: null,
  review_note: null,
  publication_status: "not_published",
  published_at: null,
  supersedes_version_id: null,
  markdown_sha256: "a".repeat(64),
  created_at: 1,
  updated_at: 1,
  is_current: false,
};

const approvedVersion = { ...awaitingVersion, review_status: "review_approved" as const, reviewed_by: 1, reviewed_at: 2 };

describe("TranscriptionVersionPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listTranscriptVersions.mockResolvedValue([awaitingVersion]);
    mocks.previewTranscriptVersion.mockResolvedValue({ version_id: awaitingVersion.version_id, markdown: "说话人 1 00:00:00\n培训开始\n", markdown_sha256: awaitingVersion.markdown_sha256 });
    mocks.reviewTranscriptVersion.mockResolvedValue(approvedVersion);
    mocks.publishTranscriptVersion.mockResolvedValue({ version: { ...approvedVersion, publication_status: "publishing" }, job: null, reused: false });
  });

  it("loads lazily and previews immutable Markdown", async () => {
    render(<TranscriptionVersionPanel mediaId="media-1" />);
    expect(mocks.listTranscriptVersions).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "审阅转录版本" }));
    expect(await screen.findByRole("button", { name: "预览 Markdown" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "预览 Markdown" }));
    expect(await screen.findByText(/培训开始/)).toBeInTheDocument();
  });

  it("submits review note and keeps publish disabled before approval", async () => {
    render(<TranscriptionVersionPanel mediaId="media-1" />);
    fireEvent.click(screen.getByRole("button", { name: "审阅转录版本" }));
    await screen.findByText("待审核");
    expect(screen.getByRole("button", { name: "发布" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(`审核备注 ${awaitingVersion.version_id}`), { target: { value: "已校对" } });
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    await waitFor(() => expect(mocks.reviewTranscriptVersion).toHaveBeenCalledWith(awaitingVersion.version_id, true, "已校对"));
  });

  it("rejects a version with the immutable review note", async () => {
    render(<TranscriptionVersionPanel mediaId="media-1" />);
    fireEvent.click(screen.getByRole("button", { name: "审阅转录版本" }));
    await screen.findByText("待审核");
    fireEvent.change(screen.getByLabelText(`审核备注 ${awaitingVersion.version_id}`), { target: { value: "时间轴需修正" } });
    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));
    await waitFor(() => expect(mocks.reviewTranscriptVersion).toHaveBeenCalledWith(awaitingVersion.version_id, false, "时间轴需修正"));
  });

  it("keeps publication disabled for an approved manual version", async () => {
    mocks.listTranscriptVersions.mockResolvedValue([{ ...approvedVersion, source: "manual", profile_id: null, provider_key: null, model_id: null, model_revision: null }]);
    render(<TranscriptionVersionPanel mediaId="media-1" />);
    fireEvent.click(screen.getByRole("button", { name: "审阅转录版本" }));
    expect(await screen.findByRole("button", { name: "发布" })).toBeDisabled();
  });

  it("publishes only an approved version", async () => {
    mocks.listTranscriptVersions.mockResolvedValue([approvedVersion]);
    render(<TranscriptionVersionPanel mediaId="media-1" />);
    fireEvent.click(screen.getByRole("button", { name: "审阅转录版本" }));
    const publish = await screen.findByRole("button", { name: "发布" });
    expect(publish).toBeEnabled();
    fireEvent.click(publish);
    await waitFor(() => expect(mocks.publishTranscriptVersion).toHaveBeenCalledWith(approvedVersion.version_id));
  });
});

