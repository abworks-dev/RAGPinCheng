import { createContext, useContext, useState, type ReactNode } from "react";

export type PlayerRequest = {
  mediaId: string;
  title: string;
  startSeconds: number;
  fromSource: boolean;
};

type VideoPlayerContextValue = {
  isOpen: boolean;
  currentRequest: PlayerRequest | null;
  open: (request: PlayerRequest) => void;
  close: () => void;
};

const VideoPlayerContext = createContext<VideoPlayerContextValue | null>(null);

export function VideoPlayerProvider({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const [currentRequest, setCurrentRequest] = useState<PlayerRequest | null>(null);

  const open = (request: PlayerRequest) => {
    setCurrentRequest(request);
    setIsOpen(true);
  };

  const close = () => {
    setIsOpen(false);
    setCurrentRequest(null);
  };

  return (
    <VideoPlayerContext.Provider value={{ isOpen, currentRequest, open, close }}>
      {children}
    </VideoPlayerContext.Provider>
  );
}

export function useVideoPlayer() {
  const ctx = useContext(VideoPlayerContext);
  if (!ctx) {
    throw new Error("useVideoPlayer must be used within VideoPlayerProvider");
  }
  return ctx;
}

/**
 * Convert HH:MM:SS or MM:SS timestamp string to total seconds.
 * Invalid or missing input returns 0.
 */
export function timestampToSeconds(timestamp: string | null | undefined): number {
  if (!timestamp) return 0;
  const parts = timestamp.trim().split(":").map(Number);
  // Filter out any NaN values that came from non-numeric parts
  const valid = parts.filter((n) => !Number.isNaN(n));
  if (valid.length === 3) {
    const [h, m, s] = valid;
    return h * 3600 + m * 60 + s;
  }
  if (valid.length === 2) {
    const [m, s] = valid;
    return m * 60 + s;
  }
  return 0;
}
