import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { AuthUser } from "../types";
import { AuthProvider, useAuth } from "./AuthContext";

const user: AuthUser = {
  id: 2,
  employee_id: "bim01",
  real_name: "合成资料员",
  role: "user",
  csrf_token: "csrf",
  content_permissions: ["organize"],
};

function Probe() {
  const { state, refreshUser } = useAuth();
  return (
    <div>
      <span>{state.status === "authed" ? (state.user.content_permissions || []).join(",") : state.status}</span>
      <button type="button" onClick={() => void Promise.all([refreshUser(), refreshUser()])}>并发刷新</button>
      <button type="button" onClick={() => void refreshUser().catch(() => undefined)}>失败刷新</button>
    </div>
  );
}

describe("AuthProvider refreshUser", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("coalesces concurrent identity refreshes", async () => {
    let resolveRefresh!: (value: AuthUser) => void;
    const refresh = new Promise<AuthUser>((resolve) => {
      resolveRefresh = resolve;
    });
    const me = vi.spyOn(api, "me")
      .mockResolvedValueOnce(user)
      .mockReturnValueOnce(refresh);

    render(<AuthProvider><Probe /></AuthProvider>);
    await screen.findByText("organize");
    fireEvent.click(screen.getByRole("button", { name: "并发刷新" }));

    expect(me).toHaveBeenCalledTimes(2);
    resolveRefresh({ ...user, content_permissions: ["review"] });
    await screen.findByText("review");
  });

  it("preserves the last trusted user when refresh fails", async () => {
    vi.spyOn(api, "me")
      .mockResolvedValueOnce(user)
      .mockRejectedValueOnce(new TypeError("network unavailable"));

    render(<AuthProvider><Probe /></AuthProvider>);
    await screen.findByText("organize");
    fireEvent.click(screen.getByRole("button", { name: "失败刷新" }));

    await waitFor(() => expect(api.me).toHaveBeenCalledTimes(2));
    expect(screen.getByText("organize")).toBeInTheDocument();
  });
});
