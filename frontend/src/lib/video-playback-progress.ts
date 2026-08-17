const STORAGE_KEY = "ragpincheng.video-playback-progress.v1";
const MAX_ENTRIES = 50;
const TTL_MS = 30 * 24 * 60 * 60 * 1000;

type PlaybackProgressEntry = {
  mediaId: string;
  userScope: string;
  seconds: number;
  updatedAt: number;
};

type PlaybackProgressStore = {
  version: 1;
  entries: PlaybackProgressEntry[];
};

function scopeFingerprint(value: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(36);
}

function readStore(now = Date.now()): PlaybackProgressStore {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null") as Partial<PlaybackProgressStore> | null;
    if (parsed?.version !== 1 || !Array.isArray(parsed.entries)) return { version: 1, entries: [] };
    return {
      version: 1,
      entries: parsed.entries.filter((entry): entry is PlaybackProgressEntry => (
        typeof entry?.mediaId === "string"
        && typeof entry?.userScope === "string"
        && typeof entry?.seconds === "number"
        && Number.isFinite(entry.seconds)
        && entry.seconds >= 0
        && typeof entry?.updatedAt === "number"
        && now - entry.updatedAt <= TTL_MS
      )),
    };
  } catch {
    return { version: 1, entries: [] };
  }
}

function writeStore(store: PlaybackProgressStore) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
  } catch {
    // Playback remains available when storage is unavailable or full.
  }
}

export function getPlaybackProgress(mediaId: string, userScope: string, now = Date.now()): number | null {
  const fingerprint = scopeFingerprint(userScope);
  const entry = readStore(now).entries.find((candidate) => (
    candidate.mediaId === mediaId && candidate.userScope === fingerprint
  ));
  return entry?.seconds ?? null;
}

export function savePlaybackProgress(mediaId: string, userScope: string, seconds: number, now = Date.now()) {
  if (!Number.isFinite(seconds) || seconds < 0) return;
  const fingerprint = scopeFingerprint(userScope);
  const entries = readStore(now).entries
    .filter((entry) => !(entry.mediaId === mediaId && entry.userScope === fingerprint));
  entries.unshift({ mediaId, userScope: fingerprint, seconds, updatedAt: now });
  writeStore({ version: 1, entries: entries.slice(0, MAX_ENTRIES) });
}

export function clearPlaybackProgress(mediaId: string, userScope: string, now = Date.now()) {
  const fingerprint = scopeFingerprint(userScope);
  const store = readStore(now);
  const entries = store.entries.filter((entry) => !(entry.mediaId === mediaId && entry.userScope === fingerprint));
  if (entries.length !== store.entries.length) writeStore({ version: 1, entries });
}

export const playbackProgressStorageKey = STORAGE_KEY;
