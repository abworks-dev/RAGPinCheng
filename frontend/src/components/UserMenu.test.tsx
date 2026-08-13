import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthUser } from "../types";
import { UserMenu } from "./UserMenu";

const auth = vi.hoisted(() => ({
  user: { id: 1, employee_id: "admin", real_name: "管理员", role: "admin", csrf_token: "csrf", content_permissions: [] } as AuthUser,
}));

vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({
    state: {
      status: "authed",
      user: auth.user,
    },
    logout: vi.fn(),
  }),
}));

describe("UserMenu collapsed layout", () => {
  afterEach(() => {
    auth.user = { id: 1, employee_id: "admin", real_name: "管理员", role: "admin", csrf_token: "csrf", content_permissions: [] };
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

  it("shows the management workspace to administrators", () => {
    render(<MemoryRouter><UserMenu /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: /管理员/ }));
    expect(screen.getByRole("button", { name: "管理工作台" })).toBeInTheDocument();
  });

  it("shows the content workspace to users with content permissions", () => {
    auth.user = { id: 2, employee_id: "bim01", real_name: "李工", role: "user", csrf_token: "csrf", content_permissions: ["organize"] };
    render(<MemoryRouter><UserMenu /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: /李工/ }));
    expect(screen.getByRole("button", { name: "资料工作台" })).toBeInTheDocument();
  });

  it("does not show a workspace entry to users without content permissions", () => {
    auth.user = { id: 3, employee_id: "member01", real_name: "王工", role: "user", csrf_token: "csrf", content_permissions: [] };
    render(<MemoryRouter><UserMenu /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: /王工/ }));
    expect(screen.queryByRole("button", { name: /工作台/ })).not.toBeInTheDocument();
  });
});
