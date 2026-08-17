import { useEffect, useRef, useState } from "react";
import { LoaderCircle } from "lucide-react";
import type { MediaTranscriptSegment } from "../types";
import { TranscriptPanel } from "./TranscriptPanel";
import { clearPlaybackProgress, getPlaybackProgress, savePlaybackProgress } from "../lib/video-playback-progress";

type SynchronizedVideoTranscriptProps = {
  mediaId: string;
  segments: MediaTranscriptSegment[];
  transcriptLoading: boolean;
  transcriptError: string | null;
  mediaUrl?: string;
  initialStartSeconds?: number;
  layout?: "stacked" | "split";
  playbackUserScope?: string;
};

export function SynchronizedVideoTranscript({
  mediaId,
  segments,
  transcriptLoading,
  transcriptError,
  mediaUrl,
  initialStartSeconds = 0,
  layout = "stacked",
  playbackUserScope,
}: SynchronizedVideoTranscriptProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [videoError, setVideoError] = useState<string | null>(null);
  const [currentTimeMs, setCurrentTimeMs] = useState(initialStartSeconds * 1000);
  const lastSavedAtRef = useRef(0);

  useEffect(() => {
    setIsLoading(true);
    setVideoError(null);
    setCurrentTimeMs(initialStartSeconds * 1000);
  }, [mediaId, mediaUrl, initialStartSeconds]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let metadataLoaded = false;

    function onLoadedMetadata() {
      metadataLoaded = true;
      setIsLoading(false);
      const savedSeconds = playbackUserScope ? getPlaybackProgress(mediaId, playbackUserScope) : null;
      const startSeconds = initialStartSeconds > 0 ? initialStartSeconds : savedSeconds;
      if (startSeconds !== null && startSeconds > 0 && Math.abs(video!.currentTime - startSeconds) > 0.1) {
        video!.currentTime = startSeconds;
        setCurrentTimeMs(Math.round(startSeconds * 1000));
      }
      video!.play().catch(() => undefined);
    }

    function persistProgress(force = false) {
      if (!metadataLoaded || !playbackUserScope || !Number.isFinite(video!.currentTime)) return;
      const now = Date.now();
      const nearEnd = video!.duration > 0
        && (video!.currentTime / video!.duration >= 0.95 || video!.duration - video!.currentTime < 10);
      if (nearEnd) {
        clearPlaybackProgress(mediaId, playbackUserScope, now);
        lastSavedAtRef.current = now;
      } else if (force || now - lastSavedAtRef.current >= 5000) {
        savePlaybackProgress(mediaId, playbackUserScope, video!.currentTime, now);
        lastSavedAtRef.current = now;
      }
    }

    function onTimeUpdate(event?: Event) {
      setCurrentTimeMs(Math.round(video!.currentTime * 1000));
      persistProgress(event?.type !== "timeupdate");
    }

    function onError() {
      setIsLoading(false);
      setVideoError("视频加载失败，请检查媒体状态或稍后重试");
    }

    video.addEventListener("loadedmetadata", onLoadedMetadata);
    video.addEventListener("timeupdate", onTimeUpdate);
    video.addEventListener("seeked", onTimeUpdate);
    video.addEventListener("pause", onTimeUpdate);
    video.addEventListener("ended", onTimeUpdate);
    video.addEventListener("error", onError);
    const onPageHide = () => persistProgress(true);
    window.addEventListener("pagehide", onPageHide);
    return () => {
      video.removeEventListener("loadedmetadata", onLoadedMetadata);
      video.removeEventListener("timeupdate", onTimeUpdate);
      video.removeEventListener("seeked", onTimeUpdate);
      video.removeEventListener("pause", onTimeUpdate);
      video.removeEventListener("ended", onTimeUpdate);
      video.removeEventListener("error", onError);
      window.removeEventListener("pagehide", onPageHide);
      persistProgress(true);
    };
  }, [mediaId, initialStartSeconds, playbackUserScope]);

  const videoSurface = (
    <div className={`relative w-full overflow-hidden bg-black ${layout === "split" ? "aspect-video lg:h-full lg:aspect-auto" : "video-preview-surface aspect-video"}`}>
      {isLoading && (
        <div className="absolute inset-0 z-10 flex items-center justify-center text-gray-400">
          <LoaderCircle className="size-7 animate-spin" aria-label="正在加载视频" />
        </div>
      )}
      {videoError && (
        <div className="absolute inset-0 z-10 flex items-center justify-center px-4 text-center text-sm text-red-300">
          {videoError}
        </div>
      )}
      <video
        ref={videoRef}
        src={mediaUrl ?? `/api/media/${encodeURIComponent(mediaId)}`}
        aria-label="视频播放器"
        className="h-full w-full object-contain"
        controls
        playsInline
        preload="metadata"
      />
    </div>
  );

  const transcript = (
    <TranscriptPanel
      segments={segments}
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
  );

  return layout === "split" ? (
    <div className="grid min-h-0 gap-3 lg:h-[min(24rem,45vh)] lg:grid-cols-[minmax(0,1.15fr)_minmax(20rem,0.85fr)]">
      <div className="flex min-h-0 min-w-0 overflow-hidden rounded-ui-md border border-border bg-black">{videoSurface}</div>
      <div className="flex h-72 min-h-0 min-w-0 flex-col overflow-hidden rounded-ui-md border border-border bg-background sm:h-80 lg:h-auto">
        {transcript}
      </div>
    </div>
  ) : (
    <div className="flex h-full min-h-0 flex-1 flex-col bg-card">
      {videoSurface}
      {transcript}
    </div>
  );
}
