import { describe, expect, it } from "vitest";
import { ATTACHMENT_FORMATS, DOCUMENT_ACCEPT, imageFitScale, isAttachmentFormat } from "./attachment-formats";
import { validateImageDimensions } from "./image-dimensions";
describe("attachment format and bounded image helpers", () => {
  it("supports every approved format without enabling unrelated native renderers", () => {
    for (const format of ATTACHMENT_FORMATS) { expect(isAttachmentFormat(format.toUpperCase())).toBe(true); expect(DOCUMENT_ACCEPT).toContain(`.${format}`); }
    expect(isAttachmentFormat("exe")).toBe(false);
  });
  it("fits rotated dimensions and never upscales by default", () => {
    expect(imageFitScale(2000, 1000, 1032, 532, 0)).toBe(.5);
    expect(imageFitScale(2000, 1000, 1032, 532, 90)).toBe(.25);
    expect(imageFitScale(1, 1, 1000, 1000, 0)).toBe(1);
  });
  it("rejects decompression-sized PNG before browser decoding", async () => {
    const bytes = new Uint8Array(24); const view = new DataView(bytes.buffer);
    view.setUint32(0, 0x89504e47); view.setUint32(4, 0x0d0a1a0a); view.setUint32(16, 10000); view.setUint32(20, 10000);
    const blob = { slice: () => ({ arrayBuffer: async () => bytes.buffer }) } as unknown as Blob;
    await expect(validateImageDimensions(blob)).rejects.toThrow("图片超过");
    view.setUint32(16, 200); view.setUint32(20, 100);
    await expect(validateImageDimensions(blob)).resolves.toBeUndefined();
  });
  it("rejects malformed image headers without handing them to a decoder", async () => {
    const blob = { slice: () => ({ arrayBuffer: async () => new ArrayBuffer(8) }) } as unknown as Blob;
    await expect(validateImageDimensions(blob)).rejects.toThrow("无法安全读取图片尺寸");
  });
});
