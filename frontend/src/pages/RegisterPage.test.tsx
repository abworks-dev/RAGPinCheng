import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RegisterPage } from "./RegisterPage";

const mocks = vi.hoisted(() => ({ register: vi.fn() }));

vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({ register: mocks.register }),
}));

describe("RegisterPage behavior baseline", () => {
  beforeEach(() => {
    mocks.register.mockReset();
  });

  it("trims identity fields and preserves the password when submitting", async () => {
    mocks.register.mockResolvedValueOnce(undefined);
    render(
      <MemoryRouter>
        <RegisterPage />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText("用户名（登录用，唯一）"), { target: { value: "  user-1  " } });
    fireEvent.change(screen.getByLabelText("真实姓名"), { target: { value: "  测试用户  " } });
    fireEvent.change(screen.getByLabelText("密码（至少 6 位）"), { target: { value: "secret1" } });
    fireEvent.change(screen.getByLabelText("确认密码"), { target: { value: "secret1" } });
    fireEvent.submit(screen.getByRole("button", { name: "注册" }).closest("form")!);

    await waitFor(() => expect(mocks.register).toHaveBeenCalledWith("user-1", "测试用户", "secret1"));
    expect(screen.getByRole("link", { name: "登录" })).toHaveAttribute("href", "/login");
  });

  it("rejects mismatched passwords before calling the registration API", async () => {
    render(
      <MemoryRouter>
        <RegisterPage />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText("密码（至少 6 位）"), { target: { value: "secret1" } });
    fireEvent.change(screen.getByLabelText("确认密码"), { target: { value: "secret2" } });
    fireEvent.submit(screen.getByRole("button", { name: "注册" }).closest("form")!);

    expect(await screen.findByText("两次输入的密码不一致")).toBeInTheDocument();
    expect(mocks.register).not.toHaveBeenCalled();
  });

  it("rejects a short password before calling the registration API", async () => {
    render(
      <MemoryRouter>
        <RegisterPage />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText("密码（至少 6 位）"), { target: { value: "short" } });
    fireEvent.change(screen.getByLabelText("确认密码"), { target: { value: "short" } });
    fireEvent.submit(screen.getByRole("button", { name: "注册" }).closest("form")!);

    expect(await screen.findByText("密码至少 6 位")).toBeInTheDocument();
    expect(mocks.register).not.toHaveBeenCalled();
  });

  it("shows a normalized API error and re-enables the submit button", async () => {
    mocks.register.mockRejectedValueOnce(new Error("409 Conflict: 用户名已存在"));
    render(
      <MemoryRouter>
        <RegisterPage />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText("用户名（登录用，唯一）"), { target: { value: "user-1" } });
    fireEvent.change(screen.getByLabelText("真实姓名"), { target: { value: "测试用户" } });
    fireEvent.change(screen.getByLabelText("密码（至少 6 位）"), { target: { value: "secret1" } });
    fireEvent.change(screen.getByLabelText("确认密码"), { target: { value: "secret1" } });
    fireEvent.submit(screen.getByRole("button", { name: "注册" }).closest("form")!);

    expect(await screen.findByText("用户名已存在")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "注册" })).not.toBeDisabled());
  });
});
