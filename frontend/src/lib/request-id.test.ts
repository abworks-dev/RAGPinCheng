import { afterEach, describe, expect, it, vi } from "vitest";
import { createRequestId } from "./request-id";

const UUID_V4_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("createRequestId", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("uses crypto.randomUUID when available", () => {
    const randomUUID = vi.fn(() => "11111111-1111-4111-8111-111111111111");
    vi.stubGlobal("crypto", { randomUUID });

    expect(createRequestId()).toBe("11111111-1111-4111-8111-111111111111");
    expect(randomUUID).toHaveBeenCalledOnce();
  });

  it("creates a UUID v4 with getRandomValues when randomUUID is unavailable", () => {
    const getRandomValues = vi.fn((bytes: Uint8Array) => {
      bytes.fill(0);
      return bytes;
    });
    vi.stubGlobal("crypto", { getRandomValues });

    expect(createRequestId()).toBe("00000000-0000-4000-8000-000000000000");
    expect(getRandomValues).toHaveBeenCalledOnce();
  });

  it("creates distinct UUID v4 values when Web Crypto is unavailable", () => {
    vi.stubGlobal("crypto", undefined);
    vi.spyOn(Date, "now").mockReturnValue(1_785_686_400_000);
    vi.spyOn(Math, "random").mockReturnValue(0.5);

    const first = createRequestId();
    const second = createRequestId();

    expect(first).toMatch(UUID_V4_PATTERN);
    expect(second).toMatch(UUID_V4_PATTERN);
    expect(second).not.toBe(first);
  });
});
