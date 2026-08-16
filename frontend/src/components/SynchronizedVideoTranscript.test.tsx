import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SynchronizedVideoTranscript } from "./SynchronizedVideoTranscript";

describe("SynchronizedVideoTranscript", () => {
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
