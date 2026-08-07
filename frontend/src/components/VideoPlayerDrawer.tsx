import { useEffect, useRef, useState } from "react";
import { useVideoPlayer } from "../hooks/useVideoPlayer";
import { LoaderCircle } from "lucide-react";
import { ResourcePreviewShell } from "./ResourcePreviewShell";
import { TranscriptPanel } from "./TranscriptPanel";
import { api, ApiError } from "../api/client";
import type { MediaTranscriptSegment } from "../types";

export function VideoPlayerDrawer() {
  const { isOpen, currentRequest, close } = useVideoPlayer();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [currentTimeMs, setCurrentTimeMs] = useState(0);
  const [transcript, setTranscript] = useState<MediaTranscriptSegment[]>([]);
  const [transcriptLoading, setTranscriptLoading] = useState(true);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);

  // Reset state when opening a new video
  useEffect(() => {
    if (isOpen && currentRequest) {
      setIsLoading(true);
      setError(null);
      setCurrentTimeMs(currentRequest.startSeconds * 1000);
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

  // Seek and attempt autoplay when metadata loads
  useEffect(() => {
    const video = videoRef.current;
    const request = currentRequest;
    if (!video || !isOpen || !request) return;

    function onLoadedMetadata() {
      setIsLoading(false);
      if (!request) return;
      const seconds = request.startSeconds;
      if (seconds > 0 && video && Math.abs(video.currentTime - seconds) > 0.1) {
        video.currentTime = seconds;
      }
      // Try autoplay — browser may block it
      video!.play().catch(() => {
        // Autoplay blocked — native controls remain available.
      });
    }

    function onTimeUpdate() {
      setCurrentTimeMs(Math.round(video!.currentTime * 1000));
    }

    function onError() {
      setIsLoading(false);
      setError("视频加载失败，请检查网络连接或稍后重试");
    }

    video.addEventListener("loadedmetadata", onLoadedMetadata);
    video.addEventListener("timeupdate", onTimeUpdate);
    video.addEventListener("seeked", onTimeUpdate);
    video.addEventListener("error", onError);

    return () => {
      video.removeEventListener("loadedmetadata", onLoadedMetadata);
      video.removeEventListener("timeupdate", onTimeUpdate);
      video.removeEventListener("seeked", onTimeUpdate);
      video.removeEventListener("error", onError);
    };
  }, [isOpen, currentRequest?.mediaId]);

  if (!isOpen || !currentRequest) return null;

  const videoUrl = `/api/media/${currentRequest.mediaId}`;

  return (
    <ResourcePreviewShell open={isOpen} title={currentRequest.title} subtitle={`从 ${formatTime(currentRequest.startSeconds)} 开始播放`} onClose={close}>
      <div className="flex h-full min-h-0 flex-col bg-card">
        {/* Video container — maintains 16:9 aspect ratio */}
        <div className="relative w-full bg-black aspect-video shrink-0">
          {isLoading && (
            <div className="absolute inset-0 flex items-center justify-center text-gray-400">
              <div className="flex flex-col items-center gap-2">
                <LoaderCircle className="size-8 animate-spin" aria-hidden="true" />
                <span className="text-sm">加载中...</span>
              </div>
            </div>
          )}
          {error && (
            <div className="absolute inset-0 flex items-center justify-center text-red-400">
              <span className="text-sm px-4 text-center">{error}</span>
            </div>
          )}
          <video
            ref={videoRef}
            src={videoUrl}
            className="w-full h-full object-contain"
            controls
            playsInline
            preload="metadata"
          />
        </div>

        <TranscriptPanel
          segments={transcript}
          currentTimeMs={currentTimeMs}
          loading={transcriptLoading}
          error={transcriptError}
          onSeek={(milliseconds) => {
            const video = videoRef.current;
            if (!video) return;
            video.currentTime = milliseconds / 1000;
            setCurrentTimeMs(milliseconds);
          }}
        />
      </div>
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
