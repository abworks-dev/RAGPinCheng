import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminMediaPage } from "./AdminMediaPage";

const mocks = vi.hoisted(() => ({
  listMediaAssets: vi.fn(),
  uploadMediaVideo: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  api: {
    listMediaAssets: mocks.listMediaAssets,
    uploadMediaVideo: mocks.uploadMediaVideo,
  },
}));

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
    media_id: "media-indexing",
    title: "BIM 标准宣贯",
    original_filename: "bim-standard.mp4",
    mime_type: "video/mp4",
    file_size: 2048,
    transcript_origin: "manual",
    status: "indexing",
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
    transcript_origin: "manual",
    status: "failed",
    created_at: 1785686400,
    updated_at: 1785686400,
    error: "转写格式不符合要求",
  },
  {
    media_id: "media-unknown",
    title: "等待处理",
    original_filename: "queued.mp4",
    mime_type: "video/mp4",
    file_size: 1024,
    transcript_origin: "manual",
    status: "queued",
    created_at: 1785686400,
    updated_at: 1785686400,
    error: null,
  },
];

describe("AdminMediaPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listMediaAssets.mockResolvedValue(assets);
    mocks.uploadMediaVideo.mockResolvedValue(assets[0]);
  });

  it("loads media assets and presents semantic processing states", async () => {
    render(<AdminMediaPage />);

    expect(screen.getByText("正在加载媒体资源…")).toBeInTheDocument();
    expect(await screen.findByText("项目交付培训")).toBeInTheDocument();
    expect(screen.getByText("共 4 个视频")).toBeInTheDocument();
    expect(screen.getByText("已就绪")).toHaveClass("bg-success/15");
    expect(screen.getByText("索引中")).toHaveClass("bg-warning/15");
    expect(screen.getByText("失败")).toHaveClass("bg-destructive/15");
    expect(screen.getByText("queued")).toHaveClass("bg-secondary");
    expect(screen.getByText("转写格式不符合要求")).toHaveClass("text-destructive");
    expect(mocks.listMediaAssets).toHaveBeenCalledTimes(1);
  });

  it("shows an empty state when no media has been uploaded", async () => {
    mocks.listMediaAssets.mockResolvedValue([]);

    render(<AdminMediaPage />);

    expect(await screen.findByText("暂无媒体资源")).toBeInTheDocument();
    expect(screen.getByText("上传第一个视频及其人工转写后，处理状态会显示在这里。")).toBeInTheDocument();
  });

  it("shows a list error and retries without changing the API contract", async () => {
    mocks.listMediaAssets
      .mockRejectedValueOnce(new Error("媒体服务暂不可用"))
      .mockResolvedValueOnce(assets);

    render(<AdminMediaPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("媒体资源加载失败");
    expect(screen.getByRole("alert")).toHaveTextContent("媒体服务暂不可用");

    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));

    await waitFor(() => expect(mocks.listMediaAssets).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("项目交付培训")).toBeInTheDocument();
  });

  it("requires all fields, uploads the exact files and refreshes the list", async () => {
    mocks.listMediaAssets.mockResolvedValueOnce([]).mockResolvedValueOnce(assets);
    const video = new File(["video-bytes"], "training.mp4", { type: "video/mp4" });
    const transcript = new File(["说话人 00:00:01 培训开始"], "training.md", { type: "text/markdown" });

    render(<AdminMediaPage />);
    await screen.findByText("暂无媒体资源");

    const uploadButton = screen.getByRole("button", { name: "上传并建立索引" });
    const titleInput = screen.getByLabelText("视频标题");
    const videoInput = screen.getByLabelText("视频文件");
    const transcriptInput = screen.getByLabelText("人工转写");

    expect(uploadButton).toBeDisabled();

    fireEvent.change(titleInput, { target: { value: "  项目培训视频  " } });
    fireEvent.change(videoInput, { target: { files: [video] } });
    expect(uploadButton).toBeDisabled();

    fireEvent.change(transcriptInput, { target: { files: [transcript] } });
    expect(uploadButton).toBeEnabled();
    expect(screen.getByText(/training\.mp4/)).toBeInTheDocument();
    expect(screen.getByText(/training\.md/)).toBeInTheDocument();

    fireEvent.click(uploadButton);

    await waitFor(() =>
      expect(mocks.uploadMediaVideo).toHaveBeenCalledWith(video, transcript, "项目培训视频"),
    );
    expect(await screen.findByText("项目交付培训")).toBeInTheDocument();
    expect(mocks.listMediaAssets).toHaveBeenCalledTimes(2);
    expect(titleInput).toHaveValue("");
    expect(videoInput).toHaveValue("");
    expect(transcriptInput).toHaveValue("");
  });

  it("keeps the selected upload data when the upload fails", async () => {
    mocks.listMediaAssets.mockResolvedValue([]);
    mocks.uploadMediaVideo.mockRejectedValueOnce(new Error("上传服务暂不可用"));
    const video = new File(["video-bytes"], "training.mp4", { type: "video/mp4" });
    const transcript = new File(["说话人 00:00:01 培训开始"], "training.md", { type: "text/markdown" });

    render(<AdminMediaPage />);
    await screen.findByText("暂无媒体资源");

    const titleInput = screen.getByLabelText("视频标题");
    fireEvent.change(titleInput, { target: { value: "项目培训视频" } });
    fireEvent.change(screen.getByLabelText("视频文件"), { target: { files: [video] } });
    fireEvent.change(screen.getByLabelText("人工转写"), { target: { files: [transcript] } });
    fireEvent.click(screen.getByRole("button", { name: "上传并建立索引" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("视频上传失败");
    expect(screen.getByRole("alert")).toHaveTextContent("上传服务暂不可用");
    expect(titleInput).toHaveValue("项目培训视频");
    expect(screen.getByRole("button", { name: "上传并建立索引" })).toBeEnabled();
    expect(mocks.listMediaAssets).toHaveBeenCalledTimes(1);
  });
});
