// Inspect bounded headers before asking the browser to allocate a decoded bitmap.
export async function validateImageDimensions(blob: Blob): Promise<void> {
  const bytes = new Uint8Array(await blob.slice(0, 512 * 1024).arrayBuffer());
  const view = new DataView(bytes.buffer);
  let width = 0; let height = 0;
  if (bytes.length >= 24 && view.getUint32(0) === 0x89504e47 && view.getUint32(4) === 0x0d0a1a0a) {
    width = view.getUint32(16); height = view.getUint32(20);
  } else if (bytes[0] === 0xff && bytes[1] === 0xd8) {
    let offset = 2;
    while (offset + 9 < bytes.length) {
      if (bytes[offset] !== 0xff) break;
      const marker = bytes[offset + 1];
      if (marker === 0xff) { offset++; continue; }
      if (marker === 0xd9 || marker === 0xda) break;
      const length = view.getUint16(offset + 2);
      if (length < 2) break;
      if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker)) {
        height = view.getUint16(offset + 5); width = view.getUint16(offset + 7); break;
      }
      offset += 2 + length;
    }
  }
  if (!width || !height) throw new Error("无法安全读取图片尺寸，请下载后查看");
  if (width * height > 80_000_000 || width > 32_768 || height > 32_768) throw new Error("图片超过轻量预览尺寸限制（8000 万像素 / 单边 32768），请下载后查看");
}
