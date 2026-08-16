import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAdminConversations } from "./useAdminConversations";

const mocks = vi.hoisted(() => ({ adminListAllConversations: vi.fn() }));
vi.mock("../api/client", () => ({ api: mocks }));

describe("useAdminConversations", () => {
  beforeEach(() => vi.clearAllMocks());

  it("loads the requested conversation window", async () => {
    const conversations = [{ id: "conversation-1", title: "合成对话" }];
    mocks.adminListAllConversations.mockResolvedValue({ conversations });
    const { result } = renderHook(() => useAdminConversations(50));
    await waitFor(() => expect(result.current.conversations).toEqual(conversations));
    expect(mocks.adminListAllConversations).toHaveBeenCalledWith(50);
  });

  it("surfaces a failed conversation load", async () => {
    mocks.adminListAllConversations.mockRejectedValue(new Error("对话服务不可用"));
    const { result } = renderHook(() => useAdminConversations());
    await waitFor(() => expect(result.current.error).toBe("对话服务不可用"));
    expect(result.current.loading).toBe(false);
  });
});
