import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useTheme } from "./useTheme";

describe("useTheme", () => {
  let dark = false;
  let onChange: (() => void) | undefined;

  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark");
    dark = false;
    onChange = undefined;
    vi.stubGlobal("matchMedia", vi.fn(() => ({
      get matches() { return dark; },
      addEventListener: (_event: string, listener: () => void) => { onChange = listener; },
      removeEventListener: vi.fn(),
    })));
  });

  it("defaults to system and follows system color changes", () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current[0]).toBe("system");
    expect(document.documentElement).not.toHaveClass("dark");

    dark = true;
    act(() => onChange?.());
    expect(document.documentElement).toHaveClass("dark");
  });

  it("persists an explicit theme preference", () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current[1]("dark"));
    expect(document.documentElement).toHaveClass("dark");
    expect(localStorage.getItem("pincheng-theme")).toBe("dark");
  });
});
