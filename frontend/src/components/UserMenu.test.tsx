import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthUser } from "../types";
import { UserMenu } from "./UserMenu";

const auth = vi.hoisted(() => ({
  user: { id: 1, employee_id: "admin", real_name: "管理员", role: "admin", csrf_token: "csrf", content_permissions: [] } as AuthUser,
  refreshUser: vi.fn(),
}));

vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({
    state: {
      status: "authed",
      user: auth.user,
    },
    logout: vi.fn(),
    refreshUser: auth.refreshUser,
  }),
}));

describe("UserMenu collapsed layout", () => {
  beforeEach(() => {
    auth.user = { id: 1, employee_id: "admin", real_name: "管理员", role: "admin", csrf_token: "csrf", content_permissions: [] };
    auth.refreshUser.mockReset();
    auth.refreshUser.mockImplementation(async () => auth.user);
  });
  it("keeps the avatar circular and centers it in the collapsed sidebar", () => {
    render(
      <MemoryRouter>
        <UserMenu collapsed />
      </MemoryRouter>,
    );

    const button = screen.getByTitle("管理员");
    const avatar = screen.getByText("管");

    expect(button).toHaveClass("w-10", "justify-center", "p-1");
    expect(avatar).toHaveClass("h-8", "w-8", "shrink-0", "rounded-full");
  });

  it("shows the management workspace to administrators", async () => {
    render(<MemoryRouter><UserMenu /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: /管理员/ }));
    expect(await screen.findByRole("button", { name: "管理工作台" })).toBeInTheDocument();
  });

  it("shows the content workspace to users with content permissions", async () => {
    auth.user = { id: 2, employee_id: "bim01", real_name: "李工", role: "user", csrf_token: "csrf", content_permissions: ["workspace.view", "item.view"] };
    render(<MemoryRouter><UserMenu /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: /李工/ }));
    expect(await screen.findByRole("button", { name: "资料工作台" })).toBeInTheDocument();
  });

  it("does not show a workspace entry to users without content permissions", async () => {
    auth.user = { id: 3, employee_id: "member01", real_name: "王工", role: "user", csrf_token: "csrf", content_permissions: [] };
    render(<MemoryRouter><UserMenu /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: /王工/ }));
    await waitFor(() => expect(auth.refreshUser).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("button", { name: /工作台/ })).not.toBeInTheDocument();
  });

  it("rechecks permission before entering the content workspace", async () => {
    auth.user = { id: 2, employee_id: "bim01", real_name: "李工", role: "user", csrf_token: "csrf", content_permissions: ["workspace.view", "item.view"] };
    auth.refreshUser
      .mockResolvedValueOnce(auth.user)
      .mockResolvedValueOnce({ ...auth.user, content_permissions: [] });
    render(<MemoryRouter><UserMenu /></MemoryRouter>);

    fireEvent.click(screen.getByRole("button", { name: /李工/ }));
    const entry = await screen.findByRole("button", { name: "资料工作台" });
    fireEvent.click(entry);

    await waitFor(() => expect(auth.refreshUser).toHaveBeenCalledTimes(2));
    expect(entry).not.toBeInTheDocument();
  });
});
