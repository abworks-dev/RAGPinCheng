import { useEffect, useState } from "react";
import { useVideoPlayer } from "../hooks/useVideoPlayer";
import { ResourcePreviewShell } from "./ResourcePreviewShell";
import { SynchronizedVideoTranscript } from "./SynchronizedVideoTranscript";
import { api, ApiError } from "../api/client";
import type { MediaTranscriptSegment } from "../types";

export function VideoPlayerDrawer() {
  const { isOpen, currentRequest, close } = useVideoPlayer();
  const [transcript, setTranscript] = useState<MediaTranscriptSegment[]>([]);
  const [transcriptLoading, setTranscriptLoading] = useState(true);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);

  // Reset state when opening a new video
  useEffect(() => {
    if (isOpen && currentRequest) {
    }
  }, [isOpen, currentRequest?.mediaId]);

  useEffect(() => {
    if (!isOpen || !currentRequest) return;
    let cancelled = false;
    setTranscript([]);
    setTranscriptLoading(true);
    setTranscriptError(null);
    api.mediaTranscript(currentRequest.mediaId)
      .then((value) => {
        if (!cancelled) setTranscript(value.segments);
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setTranscriptError(
          reason instanceof ApiError && reason.status === 404
            ? "暂无转录稿"
            : "转录稿加载失败，请稍后重试",
        );
      })
      .finally(() => {
        if (!cancelled) setTranscriptLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen, currentRequest?.mediaId]);

  useEffect(() => {
    const onPreviewOpen = (event: Event) => {
      if ((event as CustomEvent<{ kind: string }>).detail?.kind === "document") close();
    };
    window.addEventListener("resource-preview-open", onPreviewOpen);
    return () => window.removeEventListener("resource-preview-open", onPreviewOpen);
  }, [close]);

  if (!isOpen || !currentRequest) return null;

  return (
    <ResourcePreviewShell open={isOpen} title={currentRequest.title} subtitle={`从 ${formatTime(currentRequest.startSeconds)} 开始播放`} onClose={close}>
      <SynchronizedVideoTranscript
        mediaId={currentRequest.mediaId}
        segments={transcript}
        transcriptLoading={transcriptLoading}
        transcriptError={transcriptError}
        initialStartSeconds={currentRequest.startSeconds}
      />
    </ResourcePreviewShell>
  );
}

function formatTime(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = Math.floor(totalSeconds % 60);

  if (hours > 0) {
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}
