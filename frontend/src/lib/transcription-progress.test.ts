import { describe, expect, it } from "vitest";
import type { TranscriptionJob } from "../types";
import {
  audioElapsedSeconds,
  formatElapsedClock,
  transcriptionElapsedSeconds,
  transcriptionProgress,
} from "./transcription-progress";

const base: TranscriptionJob = {
  job_id: "job-1",
  media_id: "media-1",
  attempt_number: 1,
  profile_id: "profile-1",
  status: "pending",
  stage: "preparing_audio",
  processed_ms: 0,
  total_ms: null,
  failure_error_code: null,
  error_summary: null,
  failure: null,
  result_version_id: null,
  created_at: 1000,
  started_at: null,
  finished_at: null,
  updated_at: 1000,
};

function job(overrides: Partial<TranscriptionJob>): TranscriptionJob {
  return { ...base, ...overrides };
}

describe("transcriptionProgress", () => {
  it("returns 100 only for a succeeded job", () => {
    expect(transcriptionProgress(job({ status: "succeeded" }), 1000)).toBe(100);
  });

  it("returns no bar for failed or cancelled jobs", () => {
    expect(transcriptionProgress(job({ status: "failed" }), 1000)).toBeNull();
    expect(transcriptionProgress(job({ status: "cancelled" }), 1000)).toBeNull();
  });

  it("crawls 4% → 15% while extracting audio", () => {
    expect(transcriptionProgress(job({}), 1000)).toBe(4);
    expect(transcriptionProgress(job({ audio_started_at: 1000 }), 1010)).toBeCloseTo(6.5);
    expect(transcriptionProgress(job({ audio_started_at: 1000 }), 10100)).toBe(15);
  });

  it("bumps to 18% once audio extraction finished and 25% when transcription starts", () => {
    expect(transcriptionProgress(job({
      status: "running",
      stage: "validating_input",
      audio_started_at: 1000,
      audio_finished_at: 1004,
      started_at: 1004,
    }), 1004)).toBe(18);
    expect(transcriptionProgress(job({
      status: "running",
      stage: "transcribing",
      audio_started_at: 1000,
      audio_finished_at: 1004,
      started_at: 1004,
      transcribing_at: 1005,
    }), 1005)).toBe(25);
  });

  it("uses checkpoints to grow toward but never reach 96% while running", () => {
    expect(transcriptionProgress(job({
      status: "running",
      stage: "transcribing",
      total_ms: 600_000,
      processed_ms: 300_000,
    }), 2000)).toBeCloseTo(60.5);
    expect(transcriptionProgress(job({
      status: "running",
      stage: "transcribing",
      total_ms: 600_000,
      processed_ms: 599_999,
    }), 2000)).toBeLessThan(96);
  });

  it("grows by elapsed time without checkpoints and never exceeds 96%", () => {
    expect(transcriptionProgress(job({
      status: "running",
      stage: "transcribing",
      transcribing_at: 2000,
    }), 2060)).toBeCloseTo(35);
    expect(transcriptionProgress(job({
      status: "running",
      stage: "transcribing",
      transcribing_at: 2000,
    }), 1_000_000)).toBe(96);
  });

  it("pins normalizing/formatting just below completion", () => {
    expect(transcriptionProgress(job({
      status: "running",
      stage: "formatting",
    }), 2000)).toBe(96);
  });
});

describe("audioElapsedSeconds", () => {
  it("counts up while extracting and freezes after extraction", () => {
    expect(audioElapsedSeconds(job({}), 2000)).toBeNull();
    expect(audioElapsedSeconds(job({ audio_started_at: 1000 }), 1012)).toBe(12);
    expect(audioElapsedSeconds(job({ audio_started_at: 1000, audio_finished_at: 1015 }), 10_000)).toBe(15);
  });
});

describe("transcriptionElapsedSeconds", () => {
  it("counts up while transcribing and freezes at completion", () => {
    expect(transcriptionElapsedSeconds(job({ status: "running", stage: "transcribing" }), 2000)).toBeNull();
    expect(transcriptionElapsedSeconds(job({ transcribing_at: 2000 }), 2015)).toBe(15);
    expect(transcriptionElapsedSeconds(job({ transcribing_at: 2000, finished_at: 2030 }), 10_000)).toBe(30);
  });
});

describe("formatElapsedClock", () => {
  it("always includes seconds", () => {
    expect(formatElapsedClock(8)).toBe("8秒");
    expect(formatElapsedClock(187)).toBe("3分7秒");
    expect(formatElapsedClock(3725)).toBe("1小时2分5秒");
    expect(formatElapsedClock(-5)).toBe("0秒");
  });
});