import { useEffect, useRef, useState } from "react";
import { useVideoPlayer } from "../hooks/useVideoPlayer";
import { LoaderCircle, Pause, Play, Volume2, VolumeX } from "lucide-react";
import { ResourcePreviewShell } from "./ResourcePreviewShell";

export function VideoPlayerDrawer() {
  const { isOpen, currentRequest, close } = useVideoPlayer();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [seeked, setSeeked] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Reset state when opening a new video
  useEffect(() => {
    if (isOpen && currentRequest) {
      setSeeked(false);
      setIsPlaying(false);
      setIsLoading(true);
      setError(null);
    }
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
      setSeeked(true);
      // Try autoplay — browser may block it
      video!.play().catch(() => {
        // Autoplay blocked — stay paused, UI shows play button
        setIsPlaying(false);
      });
    }

    function onPlay() {
      setIsPlaying(true);
    }

    function onPause() {
      setIsPlaying(false);
    }

    function onError() {
      setIsLoading(false);
      setError("视频加载失败，请检查网络连接或稍后重试");
    }

    video.addEventListener("loadedmetadata", onLoadedMetadata);
    video.addEventListener("play", onPlay);
    video.addEventListener("pause", onPause);
    video.addEventListener("error", onError);

    return () => {
      video.removeEventListener("loadedmetadata", onLoadedMetadata);
      video.removeEventListener("play", onPlay);
      video.removeEventListener("pause", onPause);
      video.removeEventListener("error", onError);
    };
  }, [isOpen, currentRequest?.mediaId]);

  // Toggle play/pause
  function togglePlay() {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      video.play().catch(() => {});
    } else {
      video.pause();
    }
  }

  // Toggle mute
  function toggleMute() {
    const video = videoRef.current;
    if (!video) return;
    video.muted = !video.muted;
    setIsMuted(video.muted);
  }

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

        {/* Info section */}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="text-sm text-gray-600 dark:text-gray-400 space-y-2">
            <p>
              从 <span className="font-mono font-medium text-gray-900 dark:text-gray-100">
                {formatTime(currentRequest.startSeconds)}
              </span> 开始播放
            </p>
            {currentRequest.fromSource && (
              <p className="text-xs text-gray-500 dark:text-gray-500">
                点击来源卡片的「播放」按钮打开
              </p>
            )}
          </div>

          {/* Quick controls below the info */}
          <div className="mt-4 flex items-center gap-3">
            <button
              onClick={togglePlay}
              className="inline-flex items-center gap-2 rounded-ui-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:opacity-90"
            >
              {isPlaying ? <><Pause className="size-4" />暂停</> : <><Play className="size-4" />播放</>}
            </button>
            <button
              onClick={toggleMute}
              className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
              title={isMuted ? "取消静音" : "静音"}
              aria-label={isMuted ? "取消静音" : "静音"}
            >
              {isMuted ? <VolumeX className="size-5" /> : <Volume2 className="size-5" />}
            </button>
          </div>
        </div>
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
