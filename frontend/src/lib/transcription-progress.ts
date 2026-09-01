import type { TranscriptionJob } from "../types";

/**
 * Segmented transcription progress, always short of 100% while the job is
 * still running:
 *
 * - audio extraction: crawls 4% → 15% by elapsed time ("a bit of progress");
 * - audio extracted (validating_input): 18% baseline;
 * - transcription started (transcribing): 25% baseline;
 * - transcription with real checkpoints: 25% → 96% by processed/total;
 * - transcription without checkpoints: 25% → 96% by elapsed time;
 * - normalizing / formatting tail: pinned just below completion;
 * - succeeded: exactly 100%. Failed/cancelled: no bar.
 */
export const TRANSCRIPTION_RUNNING_MAX = 96;

export function transcriptionProgress(job: TranscriptionJob, nowSec: number): number | null {
  if (job.status === "succeeded") return 100;
  if (job.status !== "pending" && job.status !== "running") return null;

  const preparing = job.status === "pending" || job.stage === "preparing_audio";
  if (preparing) {
    const started = job.audio_started_at ?? job.created_at;
    const elapsed = Math.max(0, nowSec - started);
    return Math.min(15, 4 + elapsed / 4);
  }
  if (job.stage === "validating_input") return 18;

  const totalMs = job.total_ms;
  const hasCheckpoint = totalMs != null && totalMs > 0 && job.processed_ms > 0 && job.processed_ms < totalMs;
  if (hasCheckpoint) {
    return 25 + (TRANSCRIPTION_RUNNING_MAX - 25) * (job.processed_ms / totalMs);
  }
  if (job.stage === "transcribing") {
    const started = job.transcribing_at ?? job.started_at ?? job.created_at;
    const elapsed = Math.max(0, nowSec - started);
    return Math.min(TRANSCRIPTION_RUNNING_MAX, 25 + elapsed / 6);
  }
  return TRANSCRIPTION_RUNNING_MAX;
}

/** Audio extraction elapsed seconds; frozen once extraction finished. */
export function audioElapsedSeconds(job: TranscriptionJob, nowSec: number): number | null {
  if (job.audio_started_at == null) return null;
  const end = job.audio_finished_at ?? nowSec;
  return Math.max(0, end - job.audio_started_at);
}

/** Transcription elapsed seconds since the transcribing stage; frozen at completion. */
export function transcriptionElapsedSeconds(job: TranscriptionJob, nowSec: number): number | null {
  if (job.transcribing_at == null) return null;
  const end = job.finished_at ?? nowSec;
  return Math.max(0, end - job.transcribing_at);
}

/** Clock-style duration, always showing seconds (e.g. 3分7秒, 1小时2分5秒). */
export function formatElapsedClock(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  if (hours > 0) return `${hours}小时${minutes}分${rest}秒`;
  if (minutes > 0) return `${minutes}分${rest}秒`;
  return `${rest}秒`;
}