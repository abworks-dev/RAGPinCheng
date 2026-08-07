import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useTranscriptionJobs } from "./useTranscriptionJobs";

const mocks = vi.hoisted(() => ({ listTranscriptionJobs: vi.fn() }));
vi.mock("../api/client", () => ({ api: mocks }));

const runningJob = {
  job_id: "job-1",
  media_id: "media-1",
  attempt_number: 1,
  profile_id: "profile-1",
  status: "running" as const,
  stage: "transcribing",
  processed_ms: 100,
  total_ms: 1000,
  failure_error_code: null,
  error_summary: null,
  result_version_id: null,
  created_at: 1,
  started_at: 2,
  finished_at: null,
  updated_at: 2,
};

const succeededJob = { ...runningJob, status: "succeeded" as const, processed_ms: 1000, result_version_id: "version-1" };

describe("useTranscriptionJobs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listTranscriptionJobs.mockResolvedValue([runningJob]);
  });
  afterEach(() => vi.useRealTimers());

  it("discovers latest jobs and indexes them by media", async () => {
    const { result } = renderHook(() => useTranscriptionJobs());
    await waitFor(() => expect(result.current.jobs).toEqual([runningJob]));
    expect(result.current.jobsByMediaId.get("media-1")).toEqual(runningJob);
    expect(mocks.listTranscriptionJobs).toHaveBeenCalledWith(true, 100);
  });

  it("polls active jobs and stops after a terminal result", async () => {
    vi.useFakeTimers();
    mocks.listTranscriptionJobs
      .mockResolvedValueOnce([runningJob])
      .mockResolvedValueOnce([succeededJob]);
    const { result } = renderHook(() => useTranscriptionJobs());
    await act(async () => { await Promise.resolve(); });
    expect(result.current.jobs[0]?.status).toBe("running");

    await act(async () => {
      vi.advanceTimersByTime(3000);
      await Promise.resolve();
    });
    expect(result.current.jobs[0]?.status).toBe("succeeded");
    await act(async () => { vi.advanceTimersByTime(6000); await Promise.resolve(); });
    expect(mocks.listTranscriptionJobs).toHaveBeenCalledTimes(2);
  });

  it("keeps the last known jobs when background polling fails", async () => {
    vi.useFakeTimers();
    mocks.listTranscriptionJobs.mockResolvedValueOnce([runningJob]).mockRejectedValueOnce(new Error("offline"));
    const { result } = renderHook(() => useTranscriptionJobs());
    await act(async () => { await Promise.resolve(); });
    await act(async () => { vi.advanceTimersByTime(3000); await Promise.resolve(); });
    expect(result.current.jobs).toEqual([runningJob]);
    expect(result.current.error).toBeNull();
  });

  it("replaces an existing media job without duplicating it", async () => {
    const { result } = renderHook(() => useTranscriptionJobs());
    await waitFor(() => expect(result.current.jobs).toHaveLength(1));
    act(() => result.current.replaceJob(succeededJob));
    expect(result.current.jobs).toEqual([succeededJob]);
  });

  it("does not let an older refresh overwrite a locally discovered upload job", async () => {
    let resolveRefresh!: (jobs: any[]) => void;
    const pendingRefresh = new Promise<any[]>((resolve) => { resolveRefresh = resolve; });
    mocks.listTranscriptionJobs.mockReturnValueOnce(pendingRefresh);
    const { result } = renderHook(() => useTranscriptionJobs());

    act(() => result.current.replaceJob(succeededJob));
    expect(result.current.jobs).toEqual([succeededJob]);

    await act(async () => {
      resolveRefresh([]);
      await pendingRefresh;
    });
    expect(result.current.jobs).toEqual([succeededJob]);
  });
});
