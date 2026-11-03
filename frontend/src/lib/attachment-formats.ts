export const ATTACHMENT_FORMATS = ["jpg", "jpeg", "png", "dwg", "dxf", "rvt", "che", "nwd", "nwc", "ifc", "skp", "max"] as const;
export const isAttachmentFormat = (format: string) => (ATTACHMENT_FORMATS as readonly string[]).includes(format.toLowerCase());
export const DOCUMENT_ACCEPT = ".pdf,.md,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.xmind," + ATTACHMENT_FORMATS.map((format) => `.${format}`).join(",");
export const MAX_PREVIEW_BYTES = 32 * 1024 * 1024;
export const PREVIEW_TIMEOUT_MS = 30_000;
export function imageFitScale(width: number, height: number, viewportWidth: number, viewportHeight: number, rotation: number) {
  const swapped = Math.abs(rotation % 180) === 90;
  return Math.min(1, Math.max(0.01, Math.min((viewportWidth - 32) / (swapped ? height : width), (viewportHeight - 32) / (swapped ? width : height))));
}
