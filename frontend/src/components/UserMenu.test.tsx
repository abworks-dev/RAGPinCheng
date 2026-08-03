import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { UserMenu } from "./UserMenu";

vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({
    state: {
      status: "authed",
      user: { employee_id: "admin", real_name: "管理员", role: "admin" },
    },
    logout: vi.fn(),
  }),
}));

describe("UserMenu collapsed layout", () => {
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
});
