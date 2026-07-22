import { useEffect, useRef, useState } from "react";
import { useVideoPlayer } from "../hooks/useVideoPlayer";

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
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm transition-opacity duration-200"
        onClick={close}
      />

      {/* Drawer — desktop right side, mobile bottom */}
      <div className="fixed z-50 inset-x-0 bottom-0 h-[70vh] sm:inset-y-0 sm:right-0 sm:inset-x-auto sm:w-[520px] sm:h-full bg-white dark:bg-gray-900 shadow-xl flex flex-col transition-transform duration-300 ease-out">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800 shrink-0">
          <h3 className="font-medium text-gray-900 dark:text-gray-100 truncate pr-4">
            {currentRequest.title}
          </h3>
          <button
            onClick={close}
            className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
            title="关闭"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Video container — maintains 16:9 aspect ratio */}
        <div className="relative w-full bg-black aspect-video shrink-0">
          {isLoading && (
            <div className="absolute inset-0 flex items-center justify-center text-gray-400">
              <div className="flex flex-col items-center gap-2">
                <svg className="w-8 h-8 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                    strokeOpacity={0.25}
                  />
                  <path
                    fill="currentColor"
                    fillOpacity={0.75}
                    d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z"
                  />
                </svg>
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
        <div className="p-4 flex-1 overflow-y-auto">
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
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors"
            >
              {isPlaying ? "暂停" : "播放"}
            </button>
            <button
              onClick={toggleMute}
              className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
              title={isMuted ? "取消静音" : "静音"}
            >
              {isMuted ? (
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M11 5L6 9H2v6h4l5 4V5z" />
                  <path d="M19.07 4.93a10 10 0 010 14.14M15.54 8.46a5 5 0 010 7.07" />
                </svg>
              ) : (
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M11 5L6 9H2v6h4l5 4V5z" />
                  <path d="M15.54 8.46a5 5 0 010 7.07" />
                  <path d="M19.07 4.93a10 10 0 010 14.14" />
                </svg>
              )}
            </button>
          </div>
        </div>
      </div>
    </>
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
