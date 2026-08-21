import { renderHook, act } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useManagedContentLiveRefresh } from "./useManagedContentLiveRefresh";

describe("useManagedContentLiveRefresh", () => {
  it("refreshes active work and stops after it becomes idle", () => {
    vi.useFakeTimers();
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { rerender, unmount } = renderHook(({ active }) => useManagedContentLiveRefresh({ active, refresh }), {
      initialProps: { active: true },
    });

    act(() => vi.advanceTimersByTime(2000));
    expect(refresh).toHaveBeenCalledTimes(1);
    rerender({ active: false });
    act(() => vi.advanceTimersByTime(4000));
    expect(refresh).toHaveBeenCalledTimes(1);
    unmount();
    vi.useRealTimers();
  });

  it("pauses while hidden and refreshes immediately when visible again", () => {
    vi.useFakeTimers();
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { unmount } = renderHook(() => useManagedContentLiveRefresh({ active: true, refresh }));

    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    act(() => vi.advanceTimersByTime(4000));
    expect(refresh).not.toHaveBeenCalled();

    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(refresh).toHaveBeenCalledTimes(1);
    unmount();
    vi.useRealTimers();
  });
});
