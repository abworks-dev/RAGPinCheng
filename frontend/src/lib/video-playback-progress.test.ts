import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearPlaybackProgress,
  getPlaybackProgress,
  playbackProgressStorageKey,
  savePlaybackProgress,
} from "./video-playback-progress";

describe("video playback progress storage", () => {
  beforeEach(() => localStorage.clear());

  it("keeps progress isolated by media and user without storing the raw user scope", () => {
    savePlaybackProgress("media-1", "user:42", 12, 1000);

    expect(getPlaybackProgress("media-1", "user:42", 1001)).toBe(12);
    expect(getPlaybackProgress("media-1", "user:43", 1001)).toBeNull();
    expect(localStorage.getItem(playbackProgressStorageKey)).not.toContain("user:42");
  });

  it("drops expired and malformed values without interrupting playback", () => {
    savePlaybackProgress("media-1", "user:42", 12, 1000);
    expect(getPlaybackProgress("media-1", "user:42", 31 * 24 * 60 * 60 * 1000)).toBeNull();

    localStorage.setItem(playbackProgressStorageKey, "not-json");
    expect(getPlaybackProgress("media-1", "user:42")).toBeNull();
  });

  it("caps the store at 50 most recently updated entries and supports clearing", () => {
    for (let index = 0; index < 51; index += 1) {
      savePlaybackProgress(`media-${index}`, "user:42", index, 1000 + index);
    }

    const stored = JSON.parse(localStorage.getItem(playbackProgressStorageKey) ?? "{}");
    expect(stored.entries).toHaveLength(50);
    expect(getPlaybackProgress("media-0", "user:42", 2000)).toBeNull();
    clearPlaybackProgress("media-50", "user:42", 2000);
    expect(getPlaybackProgress("media-50", "user:42", 2000)).toBeNull();
  });

  it("silently handles unavailable storage", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementationOnce(() => { throw new Error("disabled"); });
    expect(() => savePlaybackProgress("media-1", "user:42", 12)).not.toThrow();
  });
});
