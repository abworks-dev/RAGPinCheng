import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { TranscriptionJob } from "../types";

const ACTIVE_STATUSES = new Set(["pending", "running"]);

export function useTranscriptionJobs() {
  const [jobs, setJobs] = useState<TranscriptionJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const requestInFlight = useRef(false);
  const localRevision = useRef(0);

  const refreshJobs = useCallback(async (surfaceError = true) => {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    const revisionAtStart = localRevision.current;
    try {
      const latest = await api.listTranscriptionJobs(true, 100);
      if (revisionAtStart === localRevision.current) {
        setJobs(latest);
        setError(null);
      }
    } catch (caught: any) {
      if (surfaceError) setError(caught?.message || String(caught));
    } finally {
      requestInFlight.current = false;
    }
  }, []);

  useEffect(() => {
    void refreshJobs();
  }, [refreshJobs]);

  const hasActive = jobs.some((job) => ACTIVE_STATUSES.has(job.status));
  useEffect(() => {
    if (!hasActive) return;
    const timer = window.setInterval(() => void refreshJobs(false), 3000);
    return () => window.clearInterval(timer);
  }, [hasActive, refreshJobs]);

  const jobsByMediaId = useMemo(
    () => new Map(jobs.map((job) => [job.media_id, job])),
    [jobs],
  );

  const replaceJob = useCallback((job: TranscriptionJob) => {
    localRevision.current += 1;
    setJobs((current) => [job, ...current.filter((item) => item.media_id !== job.media_id)]);
    setError(null);
  }, []);

  return { jobs, jobsByMediaId, error, refreshJobs, replaceJob };
}
