import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SynchronizedVideoTranscript } from "./SynchronizedVideoTranscript";
import { getPlaybackProgress, playbackProgressStorageKey, savePlaybackProgress } from "../lib/video-playback-progress";

describe("SynchronizedVideoTranscript", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });
  it("uses the supplied media URL for protected workbench playback", () => {
    render(
      <SynchronizedVideoTranscript
        mediaId="media-1"
        mediaUrl="/api/admin/media/media-1/preview"
        segments={[]}
        transcriptLoading={false}
        transcriptError={null}
      />,
    );

    expect(screen.getByLabelText("视频播放器")).toHaveAttribute(
      "src",
      "/api/admin/media/media-1/preview",
    );
  });

  it("restores saved progress when no explicit timestamp is supplied", () => {
    savePlaybackProgress("media-1", "user:42", 23);
    render(
      <SynchronizedVideoTranscript mediaId="media-1" playbackUserScope="user:42" segments={[]} transcriptLoading={false} transcriptError={null} />,
    );
    const video = screen.getByLabelText("视频播放器");
    let currentTime = 0;
    Object.defineProperty(video, "currentTime", { configurable: true, get: () => currentTime, set: (value: number) => { currentTime = value; } });
    Object.defineProperty(video, "play", { configurable: true, value: vi.fn().mockResolvedValue(undefined) });

    fireEvent.loadedMetadata(video);

    expect(currentTime).toBe(23);
  });

  it("does not overwrite saved progress when closed before metadata loads", () => {
    savePlaybackProgress("media-1", "user:42", 23);
    const { unmount } = render(
      <SynchronizedVideoTranscript mediaId="media-1" playbackUserScope="user:42" segments={[]} transcriptLoading={false} transcriptError={null} />,
    );

    unmount();

    expect(getPlaybackProgress("media-1", "user:42")).toBe(23);
  });

  it("prefers an explicit timestamp and clears progress near the end", () => {
    savePlaybackProgress("media-1", "user:42", 23);
    render(
      <SynchronizedVideoTranscript mediaId="media-1" playbackUserScope="user:42" initialStartSeconds={5} segments={[]} transcriptLoading={false} transcriptError={null} />,
    );
    const video = screen.getByLabelText("视频播放器");
    let currentTime = 0;
    Object.defineProperty(video, "currentTime", { configurable: true, get: () => currentTime, set: (value: number) => { currentTime = value; } });
    Object.defineProperty(video, "duration", { configurable: true, value: 100 });
    Object.defineProperty(video, "play", { configurable: true, value: vi.fn().mockResolvedValue(undefined) });
    fireEvent.loadedMetadata(video);
    expect(currentTime).toBe(5);

    currentTime = 96;
    fireEvent.timeUpdate(video);
    expect(localStorage.getItem(playbackProgressStorageKey)).not.toContain('"mediaId":"media-1"');
  });

  it("seeks the embedded video when a timestamp cue is clicked", () => {
    render(
      <SynchronizedVideoTranscript
        mediaId="media-1"
        segments={[{ id: 0, start_ms: 5000, end_ms: null, text: "第五秒" }]}
        transcriptLoading={false}
        transcriptError={null}
        layout="split"
      />,
    );

    const video = screen.getByLabelText("视频播放器");
    let currentTime = 0;
    Object.defineProperty(video, "currentTime", {
      configurable: true,
      get: () => currentTime,
      set: (value: number) => { currentTime = value; },
    });

    fireEvent.click(screen.getByRole("button", { name: "跳转到 00:05" }));

    expect(currentTime).toBe(5);
  });
});
