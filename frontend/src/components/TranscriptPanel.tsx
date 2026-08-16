import { useEffect, useMemo, useRef, useState } from "react";
import { LocateFixed } from "lucide-react";
import type { MediaTranscriptSegment } from "../types";

type TranscriptPanelProps = {
  segments: MediaTranscriptSegment[];
  currentTimeMs: number;
  loading: boolean;
  error: string | null;
  onSeek: (milliseconds: number) => void;
};

export function findActiveSegmentIndex(
  segments: MediaTranscriptSegment[],
  currentTimeMs: number,
): number {
  let low = 0;
  let high = segments.length - 1;
  let candidate = -1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (segments[middle].start_ms <= currentTimeMs) {
      candidate = middle;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  if (candidate < 0) return -1;
  const end = segments[candidate].end_ms;
  return end === null || currentTimeMs < end ? candidate : -1;
}

export function TranscriptPanel({
  segments,
  currentTimeMs,
  loading,
  error,
  onSeek,
}: TranscriptPanelProps) {
  const [following, setFollowing] = useState(true);
  const listRef = useRef<HTMLDivElement>(null);
  const cueRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const activeIndex = useMemo(
    () => findActiveSegmentIndex(segments, currentTimeMs),
    [segments, currentTimeMs],
  );

  useEffect(() => {
    setFollowing(true);
  }, [segments]);

  useEffect(() => {
    if (!following || activeIndex < 0) return;
    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const cue = cueRefs.current[activeIndex];
    if (typeof cue?.scrollIntoView !== "function") return;
    cue.scrollIntoView({
      behavior: reducedMotion ? "auto" : "smooth",
      block: "center",
    });
  }, [activeIndex, following]);

  function suspendFollowing() {
    if (activeIndex >= 0) setFollowing(false);
  }

  function resumeFollowing() {
    setFollowing(true);
    const cue = cueRefs.current[activeIndex];
    if (typeof cue?.scrollIntoView !== "function") return;
    cue.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col" aria-label="视频转录稿">
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-4">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-foreground">转录稿</h3>
          {!loading && !error && segments.length > 0 && (
            <span className="text-xs text-muted-foreground">{segments.length} 段</span>
          )}
        </div>
        <span className="text-xs text-muted-foreground">
          {following ? "自动跟随" : "已暂停跟随"}
        </span>
      </div>

      <div
        ref={listRef}
        className="relative min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-3"
        onWheel={suspendFollowing}
        onTouchStart={suspendFollowing}
        onKeyDown={(event) => {
          if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End"].includes(event.key)) {
            suspendFollowing();
          }
        }}
        tabIndex={0}
      >
        {loading && <p className="px-3 py-8 text-center text-sm text-muted-foreground">正在加载转录稿…</p>}
        {!loading && error && <p className="px-3 py-8 text-center text-sm text-muted-foreground">{error}</p>}
        {!loading && !error && segments.length === 0 && (
          <p className="px-3 py-8 text-center text-sm text-muted-foreground">暂无转录稿</p>
        )}
        {!loading && !error && segments.map((segment, index) => {
          const active = index === activeIndex;
          return (
            <button
              key={segment.id}
              ref={(node) => { cueRefs.current[index] = node; }}
              type="button"
              aria-current={active ? "true" : undefined}
              aria-label={`跳转到 ${formatTranscriptTime(segment.start_ms)}`}
              onClick={() => {
                setFollowing(true);
                onSeek(segment.start_ms);
              }}
              className={`mb-1 grid w-full grid-cols-[3.5rem_1fr] gap-3 rounded-lg border-l-2 px-3 py-2.5 text-left transition-colors ${
                active
                  ? "border-blue-500 bg-blue-500/10 text-foreground"
                  : "border-transparent text-muted-foreground hover:bg-muted/70 hover:text-foreground"
              }`}
            >
              <span className={`font-mono text-xs ${active ? "font-semibold text-blue-500" : ""}`}>
                {formatTranscriptTime(segment.start_ms)}
              </span>
              <span className={`whitespace-pre-wrap text-sm leading-6 ${active ? "font-medium" : ""}`}>
                {segment.text}
              </span>
            </button>
          );
        })}
        {!following && activeIndex >= 0 && (
          <button
            type="button"
            onClick={resumeFollowing}
            className="sticky bottom-3 mx-auto flex items-center gap-1.5 rounded-full bg-blue-600 px-3 py-2 text-xs font-medium text-white shadow-lg hover:bg-blue-700"
          >
            <LocateFixed className="size-3.5" />
            回到当前进度
          </button>
        )}
      </div>
    </section>
  );
}

export function formatTranscriptTime(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours > 0
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}
