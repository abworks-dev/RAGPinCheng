import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import type { MediaAsset } from "../../types";
import { formatAdminDate, formatBytes } from "./admin-formatters";
const MEDIA_STATUS_LABELS: Record<string, string> = {
  uploading: "上传中",
  transcript_ready: "转写就绪",
  indexing: "索引中",
  ready: "已就绪",
  failed: "失败",
};

const MEDIA_STATUS_COLORS: Record<string, string> = {
  uploading: "bg-sky-100 text-sky-700",
  transcript_ready: "bg-blue-100 text-blue-700",
  indexing: "bg-amber-100 text-amber-700",
  ready: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
};

export function AdminMediaPage() {
  const [mediaAssets, setMediaAssets] = useState<MediaAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [transcriptFile, setTranscriptFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");

  const refresh = useCallback(async () => {
    try {
      const assets = await api.listMediaAssets();
      setMediaAssets(assets);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleUpload() {
    if (!videoFile || !transcriptFile || !title.trim()) return;
    setUploading(true);
    setError(null);
    try {
      await api.uploadMediaVideo(videoFile, transcriptFile, title.trim());
      setVideoFile(null);
      setTranscriptFile(null);
      setTitle("");
      await refresh();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setUploading(false);
    }
  }

  const canUpload = videoFile && transcriptFile && title.trim() && !uploading;

  return (
    <div className="space-y-6">
      {/* Upload card */}
      <div className="rounded-lg border border-gray-200 bg-panel p-4">
        <h2 className="font-semibold mb-3">上传视频 + 转写</h2>
        <p className="text-xs text-muted mb-4">
          上传视频（仅限 MP4 格式。请同时提供已有人工转写的 Markdown 文件。
          格式要求：每行以「说话人 HH:MM:SS」开头，后跟该时间段的内容。
          上传后系统会自动建立索引，并将视频与转写关联，供检索引用。
        </p>
        <div className="space-y-3">
          <div>
            <label className="block text-sm mb-1">视频标题</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="例如：2024年项目培训视频"
              className="w-full rounded-lg border border-gray-300 px-3 py-1.5 text-sm bg-bg"
            />
          </div>
          <div>
            <label className="block text-sm mb-1">视频文件（MP4）</label>
            <input
              type="file"
              accept=".mp4,video/mp4"
              onChange={(e) => setVideoFile(e.target.files?.[0] || null)}
              className="text-sm"
            />
            {videoFile && (
              <div className="text-xs text-muted mt-1">
                {videoFile.name} · {formatBytes(videoFile.size)}
              </div>
            )}
          </div>
          <div>
            <label className="block text-sm mb-1">转写文件（MD）</label>
            <input
              type="file"
              accept=".md,text/markdown"
              onChange={(e) => setTranscriptFile(e.target.files?.[0] || null)}
              className="text-sm"
            />
            {transcriptFile && (
              <div className="text-xs text-muted mt-1">
                {transcriptFile.name} · {formatBytes(transcriptFile.size)}
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={handleUpload}
            disabled={!canUpload}
            className="rounded-lg bg-accent text-white px-4 py-1.5 text-sm hover:opacity-90 disabled:opacity-50"
          >
            {uploading ? "上传中…" : "上传并建立索引"}
          </button>
          {error && <div className="text-sm text-red-600">{error}</div>}
        </div>
      </div>

      {/* Assets list */}
      <div className="rounded-lg border border-gray-200 bg-panel p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold">媒体资源</h2>
          <span className="text-xs text-muted">{mediaAssets.length} 个视频</span>
        </div>
        {loading && <div className="text-sm text-muted py-4">加载中…</div>}
        {!loading && mediaAssets.length === 0 && (
          <div className="text-sm text-muted py-4">
            （暂无已上传媒体 — 请在上方上传第一个视频 + 转写）
          </div>
        )}
        {mediaAssets.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-muted">
              <tr>
                <th className="text-left px-2 py-1">标题</th>
                <th className="text-left px-2 py-1">原始文件</th>
                <th className="text-left px-2 py-1">大小</th>
                <th className="text-left px-2 py-1">状态</th>
                <th className="text-left px-2 py-1">创建时间</th>
              </tr>
              </thead>
              <tbody>
              {mediaAssets.map((m) => (
                <tr key={m.media_id} className="border-t border-gray-100 dark:border-gray-800">
                  <td className="px-2 py-1.5 font-medium max-w-xs truncate" title={m.title}>
                    {m.title}
                  </td>
                  <td className="px-2 py-1.5 text-muted max-w-xs truncate" title={m.original_filename}>
                    {m.original_filename}
                  </td>
                  <td className="px-2 py-1.5 text-muted">{formatBytes(m.file_size)}</td>
                  <td className="px-2 py-1.5">
                    <span className={
                      "inline-flex px-1.5 py-0.5 rounded text-[11px] " +
                      (MEDIA_STATUS_COLORS[m.status] || "bg-gray-100 text-gray-700")
                    }>
                      {MEDIA_STATUS_LABELS[m.status] || m.status}
                    </span>
                    {m.error && <div className="text-[11px] text-red-600 mt-0.5">{m.error}</div>}
                  </td>
                  <td className="px-2 py-1.5 text-muted text-xs">{formatAdminDate(m.created_at)}</td>
                </tr>
              ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
