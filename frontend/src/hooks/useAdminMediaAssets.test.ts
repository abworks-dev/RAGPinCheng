import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAdminMediaAssets } from "./useAdminMediaAssets";

const mocks = vi.hoisted(() => ({ listMediaAssets: vi.fn() }));
vi.mock("../api/client", () => ({ api: mocks }));

describe("useAdminMediaAssets", () => {
  beforeEach(() => vi.clearAllMocks());

  it("loads assets and exposes refresh", async () => {
    const assets = [{ media_id: "media-1", title: "合成视频" }];
    mocks.listMediaAssets.mockResolvedValue(assets);
    const { result } = renderHook(() => useAdminMediaAssets());
    await waitFor(() => expect(result.current.assets).toEqual(assets));
    expect(result.current.error).toBeNull();
    await result.current.refresh();
    expect(mocks.listMediaAssets).toHaveBeenCalledTimes(2);
  });

  it("keeps an explicit error state when the listing fails", async () => {
    mocks.listMediaAssets.mockRejectedValue(new Error("媒体服务不可用"));
    const { result } = renderHook(() => useAdminMediaAssets());
    await waitFor(() => expect(result.current.error).toBe("媒体服务不可用"));
    expect(result.current.assets).toEqual([]);
    expect(result.current.loading).toBe(false);
  });
});
