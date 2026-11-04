import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AttachmentThumbnail } from "./AttachmentThumbnail";
import type { FileResult } from "../types";
const mocks = vi.hoisted(() => ({ managedAttachmentInfo: vi.fn(), managedAttachmentBytes: vi.fn(), validate: vi.fn() }));
vi.mock("../api/client", () => ({ api: mocks }));
vi.mock("../lib/image-dimensions", () => ({ validateImageDimensions: mocks.validate }));
const file: FileResult = { item_id: "item", version_id: "v1", title: "合成图", original_filename: "test.png", doc_type: "png", file_size: 24, category_key: "category", category_path: "合成", version_number: 1, matched_fields: [], snippet: "", preview_kind: "image", can_preview: true, can_download: false };
let intersect: IntersectionObserverCallback;
let disconnect: ReturnType<typeof vi.fn>;
beforeEach(() => {
  vi.clearAllMocks(); disconnect = vi.fn();
  vi.stubGlobal("IntersectionObserver", class { constructor(callback: IntersectionObserverCallback) { intersect = callback; } observe = vi.fn(); disconnect = disconnect; });
  vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:synthetic"), revokeObjectURL: vi.fn() });
  mocks.managedAttachmentInfo.mockResolvedValue({ can_preview: true, can_download: false, preview_kind: "image", file_size: 24 });
  mocks.managedAttachmentBytes.mockResolvedValue(new Blob(["synthetic"])); mocks.validate.mockResolvedValue(undefined);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function visible() { act(() => intersect([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver)); }
describe("guarded chat thumbnails", () => {
  it("waits for intersection, allows preview-only users and revokes on unmount", async () => {
    const view = render(<AttachmentThumbnail file={file} />);
    expect(mocks.managedAttachmentInfo).not.toHaveBeenCalled(); visible();
    await screen.findByRole("img");
    expect(mocks.managedAttachmentInfo).toHaveBeenCalledWith("v1", expect.any(AbortSignal), "chat");
    expect(mocks.managedAttachmentBytes).toHaveBeenCalledWith("v1", true, expect.any(AbortSignal), "chat");
    expect(mocks.validate).toHaveBeenCalled();
    view.rerender(<AttachmentThumbnail file={{ ...file }} />);
    expect(mocks.managedAttachmentInfo).toHaveBeenCalledTimes(1);
    const signal = mocks.managedAttachmentInfo.mock.calls[0][1] as AbortSignal;
    view.rerender(<AttachmentThumbnail file={{ ...file, can_preview: false }} />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(signal.aborted).toBe(true); expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:synthetic");
    view.unmount();
  });
  it("does not request unavailable or heavy previews", () => {
    render(<><AttachmentThumbnail file={{ ...file, can_preview: false }} /><AttachmentThumbnail file={{ ...file, preview_kind: "ifc" }} /><AttachmentThumbnail file={{ ...file, preview_kind: "pdf" }} /></>);
    expect(mocks.managedAttachmentInfo).not.toHaveBeenCalled(); expect(mocks.managedAttachmentBytes).not.toHaveBeenCalled();
  });
  it("does not read bytes when fresh permission denies or kind is not image", async () => {
    mocks.managedAttachmentInfo.mockResolvedValue({ can_preview: false, preview_kind: "image" });
    render(<AttachmentThumbnail file={file} />); visible();
    await waitFor(() => expect(screen.getByText("暂无图片封面")).toBeInTheDocument());
    expect(mocks.managedAttachmentBytes).not.toHaveBeenCalled();
  });
  it("shows guarded decode failure without making an object URL", async () => {
    mocks.validate.mockRejectedValue(new Error("unsafe image")); render(<AttachmentThumbnail file={file} />); visible();
    await screen.findByText("封面加载失败，可打开预览重试"); expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
  it("aborts pending metadata and ignores late results", async () => {
    let resolve!: (value: unknown) => void;
    mocks.managedAttachmentInfo.mockReturnValue(new Promise(done => { resolve = done; }));
    const view = render(<AttachmentThumbnail file={file} />); visible();
    const signal = mocks.managedAttachmentInfo.mock.calls[0][1] as AbortSignal;
    view.unmount(); expect(signal.aborted).toBe(true);
    await act(async () => resolve({ can_preview: true, preview_kind: "image" }));
    expect(mocks.managedAttachmentBytes).not.toHaveBeenCalled();
  });
});
