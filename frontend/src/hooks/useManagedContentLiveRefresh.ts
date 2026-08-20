import { useEffect, useRef } from "react";

const REFRESH_INTERVAL_MS = 2000;

type Options = {
  active: boolean;
  refresh: () => Promise<unknown> | unknown;
  enabled?: boolean;
};

export function useManagedContentLiveRefresh({ active, refresh, enabled = true }: Options) {
  const refreshRef = useRef(refresh);
  const inFlightRef = useRef(false);
  refreshRef.current = refresh;

  useEffect(() => {
    if (!enabled || !active) return undefined;
    let timer: number | undefined;
    let disposed = false;

    const run = () => {
      if (disposed || document.visibilityState !== "visible" || inFlightRef.current) return;
      inFlightRef.current = true;
      Promise.resolve(refreshRef.current()).finally(() => {
        inFlightRef.current = false;
      });
    };
    const schedule = () => {
      if (!disposed) timer = window.setTimeout(() => { run(); schedule(); }, REFRESH_INTERVAL_MS);
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") run();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    schedule();
    return () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [active, enabled]);
}
