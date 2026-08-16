import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAdminUsers } from "./useAdminUsers";

const mocks = vi.hoisted(() => ({ adminListUsers: vi.fn() }));
vi.mock("../api/client", () => ({ api: mocks }));

describe("useAdminUsers", () => {
  beforeEach(() => vi.clearAllMocks());

  it("loads users through the admin users facade", async () => {
    const users = [{ id: 1, employee_id: "synthetic-user" }];
    mocks.adminListUsers.mockResolvedValue({ users });
    const { result } = renderHook(() => useAdminUsers());
    await waitFor(() => expect(result.current.users).toEqual(users));
    expect(mocks.adminListUsers).toHaveBeenCalledTimes(1);
  });

  it("does not hide a failed user load", async () => {
    mocks.adminListUsers.mockRejectedValue(new Error("用户服务不可用"));
    const { result } = renderHook(() => useAdminUsers());
    await waitFor(() => expect(result.current.error).toBe("用户服务不可用"));
    expect(result.current.loading).toBe(false);
  });
});
