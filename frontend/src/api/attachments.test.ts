import { afterEach, describe, expect, it, vi } from "vitest";
import { api, setCsrfToken } from "./client";

afterEach(() => { setCsrfToken(null); vi.unstubAllGlobals(); });

describe("version bound attachment transport", () => {
  it("uses only dedicated chat routes for safe metadata preview and download", async () => {
    const fetcher = vi.fn().mockImplementation(async () => new Response("{}", { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetcher);
    await api.managedAttachmentInfo("version/1", undefined, "chat");
    await api.managedAttachmentBytes("version/1", true, new AbortController().signal, "chat");
    await api.downloadChatAttachment("version/1", "synthetic.png");
    expect(fetcher.mock.calls.map(call => call[0])).toEqual(["/api/attachments/versions/version%2F1/info", "/api/attachments/versions/version%2F1/preview", "/api/attachments/versions/version%2F1/download"]);
    for (const [, init] of fetcher.mock.calls) expect(init.credentials).toBe("include");
  });
  it("sends approved metadata fields and CSRF through version bound admin mutations", async () => {
    const fetcher = vi.fn().mockImplementation(async () => new Response("{}", { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetcher); setCsrfToken("synthetic-token");
    const metadata = { description: "合成描述", tags: ["图纸"], project: "合成项目", aliases: ["合成别名"] };
    await api.updateAttachmentSearchMetadata("version/1", metadata); await api.startAttachmentOcr("version/1"); await api.cancelAttachmentOcr("version/1");
    expect(fetcher.mock.calls.map(call => call[0])).toEqual(["/api/admin/content/versions/version%2F1/search-metadata", "/api/admin/content/versions/version%2F1/ocr", "/api/admin/content/versions/version%2F1/ocr/cancel"]);
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(metadata);
    for (const [, init] of fetcher.mock.calls) { expect(init.credentials).toBe("include"); expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("synthetic-token"); }
  });
  it("uses a separate sidecar endpoint with credentials and CSRF", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("{}", { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetcher); setCsrfToken("synthetic-token");
    const file = new File(["test"], "preview.png", { type: "image/png" });
    await api.uploadManagedAttachmentPreview("version/1", file);
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("/api/admin/content/versions/version%2F1/associated-preview");
    expect(init.credentials).toBe("include");
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("synthetic-token");
    expect(init.body.get("file")).toBe(file);
  });

  it("cancels advertised oversize content before reading it", async () => {
    const cancel = vi.fn();
    const response = new Response(new ReadableStream({ cancel }), { headers: { "content-length": String(33 * 1024 * 1024) } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
    await expect(api.managedAttachmentBytes("version-1", true, new AbortController().signal)).rejects.toThrow("32 MB");
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("cancels a chunked response that crosses the preview cap", async () => {
    const cancel = vi.fn();
    const response = new Response(new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(33 * 1024 * 1024)); }, cancel }));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
    await expect(api.managedAttachmentBytes("version-1", true, new AbortController().signal)).rejects.toThrow("32 MB");
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("propagates permission failure without reading preview bytes", async () => {
    const cancel = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(new ReadableStream({ cancel }), { status: 403 })));
    await expect(api.managedAttachmentBytes("version-1", true, new AbortController().signal)).rejects.toMatchObject({ status: 403 });
    expect(cancel).toHaveBeenCalledOnce();
  });
});
