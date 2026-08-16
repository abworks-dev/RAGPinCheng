import { useEffect, useRef, useState } from "react";
import { LoaderCircle } from "lucide-react";
import type { MediaTranscriptSegment } from "../types";
import { TranscriptPanel } from "./TranscriptPanel";

type SynchronizedVideoTranscriptProps = {
  mediaId: string;
  segments: MediaTranscriptSegment[];
  transcriptLoading: boolean;
  transcriptError: string | null;
  mediaUrl?: string;
  initialStartSeconds?: number;
  layout?: "stacked" | "split";
};

export function SynchronizedVideoTranscript({
  mediaId,
  segments,
  transcriptLoading,
  transcriptError,
  mediaUrl,
  initialStartSeconds = 0,
  layout = "stacked",
}: SynchronizedVideoTranscriptProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [videoError, setVideoError] = useState<string | null>(null);
  const [currentTimeMs, setCurrentTimeMs] = useState(initialStartSeconds * 1000);

  useEffect(() => {
    setIsLoading(true);
    setVideoError(null);
    setCurrentTimeMs(initialStartSeconds * 1000);
  }, [mediaId, mediaUrl, initialStartSeconds]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    function onLoadedMetadata() {
      setIsLoading(false);
      if (initialStartSeconds > 0 && Math.abs(video!.currentTime - initialStartSeconds) > 0.1) {
        video!.currentTime = initialStartSeconds;
      }
      video!.play().catch(() => undefined);
    }

    function onTimeUpdate() {
      setCurrentTimeMs(Math.round(video!.currentTime * 1000));
    }

    function onError() {
      setIsLoading(false);
      setVideoError("视频加载失败，请检查媒体状态或稍后重试");
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
  }, [mediaId, initialStartSeconds]);

  const videoSurface = (
    <div className="relative aspect-video w-full overflow-hidden bg-black">
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
    <div className="grid min-h-0 gap-3 lg:grid-cols-[minmax(0,1.15fr)_minmax(20rem,0.85fr)] lg:items-start">
      <div className="min-w-0 overflow-hidden rounded-ui-md border border-border bg-black">{videoSurface}</div>
      <div className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-ui-md border border-border bg-background lg:h-[32rem] lg:max-h-[calc(100vh-18rem)]">
        {transcript}
      </div>
    </div>
  ) : (
    <div className="flex min-h-0 flex-1 flex-col bg-card">
      {videoSurface}
      {transcript}
    </div>
  );
}
