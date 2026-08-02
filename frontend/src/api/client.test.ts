import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, setCsrfToken, setUnauthorizedHandler } from "./client";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  setCsrfToken(null);
  setUnauthorizedHandler(null);
  vi.unstubAllGlobals();
});

describe("api client", () => {
  it("sends cookies, JSON and CSRF for a mutating request", async () => {
    setCsrfToken("csrf-123");
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ id: 1, employee_id: "u1", real_name: "测试", role: "user", csrf_token: "next" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.login("u1", "password");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/login",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify({ employee_id: "u1", password: "password" }),
        headers: {
          "content-type": "application/json",
          "X-CSRF-Token": "csrf-123",
        },
      }),
    );
  });

  it("preserves GET requests without a CSRF header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ users: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await api.adminListUsers();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/users",
      expect.objectContaining({ credentials: "include", headers: {} }),
    );
  });

  it("preserves the administrator PATCH contract", async () => {
    setCsrfToken("csrf-admin");
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ id: 7, employee_id: "u7", real_name: "管理员测试", role: "admin", is_active: true }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.adminPatchUser(7, { role: "admin", is_active: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/users/7",
      expect.objectContaining({
        method: "PATCH",
        credentials: "include",
        body: JSON.stringify({ role: "admin", is_active: true }),
        headers: {
          "content-type": "application/json",
          "X-CSRF-Token": "csrf-admin",
        },
      }),
    );
  });

  it("uploads documents as FormData without forcing Content-Type", async () => {
    setCsrfToken("csrf-upload");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ accepted: [], skipped: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["document"], "规范.pdf", { type: "application/pdf" });

    await api.adminUploadDocuments([file], "公司标准", "技术规范");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/admin/upload");
    expect(init).toMatchObject({
      method: "POST",
      credentials: "include",
      headers: { "X-CSRF-Token": "csrf-upload" },
    });
    expect(init.headers).not.toHaveProperty("content-type");
    expect(init.headers).not.toHaveProperty("Content-Type");
    expect(init.body).toBeInstanceOf(FormData);
    const form = init.body as FormData;
    expect(form.get("category")).toBe("公司标准");
    expect(form.get("subcategory")).toBe("技术规范");
    expect((form.get("files") as File).name).toBe("规范.pdf");
  });

  it("preserves retry and delete index-job requests", async () => {
    setCsrfToken("csrf-jobs");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ id: 12, status: "pending" }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await api.adminRetryIndexJob(12);
    await api.adminDeleteIndexJob(12);

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/admin/index/jobs/12/retry",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: { "X-CSRF-Token": "csrf-jobs" },
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/admin/index/jobs/12",
      expect.objectContaining({
        method: "DELETE",
        credentials: "include",
        headers: { "X-CSRF-Token": "csrf-jobs" },
      }),
    );
  });

  it("invokes the 401 handler and exposes structured ApiError data", async () => {
    const unauthorized = vi.fn();
    setUnauthorizedHandler(unauthorized);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "需要重新登录" }), {
          status: 401,
          statusText: "Unauthorized",
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    const error = await api.adminStats().catch((caught) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 401,
      body: JSON.stringify({ detail: "需要重新登录" }),
    });
    expect(unauthorized).toHaveBeenCalledTimes(1);
  });
});
