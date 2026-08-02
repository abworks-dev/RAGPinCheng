import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { LoginPage } from "./LoginPage";

const mocks = vi.hoisted(() => ({ login: vi.fn() }));

vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({ login: mocks.login }),
}));

describe("LoginPage behavior baseline", () => {
  it("trims the employee id and preserves the password when submitting", async () => {
    mocks.login.mockResolvedValueOnce(undefined);
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "  user-1  " } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "secret" } });
    fireEvent.submit(screen.getByRole("button", { name: "登录" }).closest("form")!);

    await waitFor(() => expect(mocks.login).toHaveBeenCalledWith("user-1", "secret"));
    expect(screen.getByRole("link", { name: "注册" })).toHaveAttribute("href", "/register");
  });

  it("shows a normalized API error and re-enables the submit button", async () => {
    mocks.login.mockRejectedValueOnce(new Error("401 Unauthorized: 用户名或密码错误"));
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "user-1" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "bad" } });
    fireEvent.submit(screen.getByRole("button", { name: "登录" }).closest("form")!);

    expect(await screen.findByText("用户名或密码错误")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "登录" })).not.toBeDisabled());
  });
});
