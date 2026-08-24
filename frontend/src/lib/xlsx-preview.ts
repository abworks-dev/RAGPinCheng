import type { CSSProperties } from "react";
import type ExcelJS from "exceljs";
import { format as formatExcelValue } from "ssf";

export interface PreviewCell { key: string; text: string; colSpan?: number; rowSpan?: number; style: CSSProperties; }
export interface PreviewRow { key: string; height?: number; cells: PreviewCell[]; }
export interface PreviewImage { key: string; src: string; left: number; top: number; width: number; height: number; }
export interface PreviewSheet { name: string; rows: PreviewRow[]; columnWidths: number[]; images: PreviewImage[]; }

const MAX_ROWS = 5000;
const MAX_COLS = 100;

function color(value: Partial<ExcelJS.Color> | undefined): string | undefined {
  if (!value || !("argb" in value) || !value.argb) return undefined;
  const argb = value.argb.replace(/^#/, "");
  return `#${argb.length === 8 ? argb.slice(2) : argb}`.toLowerCase();
}

function border(value: Partial<ExcelJS.Border> | undefined): string | undefined {
  if (!value?.style) return undefined;
  const width = value.style === "medium" ? 2 : value.style === "thick" ? 3 : 1;
  const line = value.style.includes("dash") ? "dashed" : value.style === "dotted" ? "dotted" : "solid";
  return `${width}px ${line} ${color(value.color) || "#000000"}`;
}

function cellStyle(cell: ExcelJS.Cell): CSSProperties {
  const fill = cell.fill?.type === "pattern" ? color(cell.fill.fgColor) : undefined;
  const horizontal = cell.alignment?.horizontal;
  const textAlign = horizontal === "centerContinuous" ? "center" :
    horizontal && ["left", "right", "center", "justify"].includes(horizontal) ? horizontal : undefined;
  return {
    fontFamily: cell.font?.name,
    fontSize: cell.font?.size ? `${cell.font.size}pt` : undefined,
    fontWeight: cell.font?.bold ? "700" : undefined,
    fontStyle: cell.font?.italic ? "italic" : undefined,
    textDecoration: cell.font?.underline ? "underline" : undefined,
    color: color(cell.font?.color), backgroundColor: fill,
    textAlign: textAlign as CSSProperties["textAlign"],
    verticalAlign: cell.alignment?.vertical,
    whiteSpace: cell.alignment?.wrapText ? "pre-wrap" : "nowrap",
    borderTop: border(cell.border?.top), borderRight: border(cell.border?.right),
    borderBottom: border(cell.border?.bottom), borderLeft: border(cell.border?.left),
  };
}

function dimensions(cell: ExcelJS.Cell): Pick<PreviewCell, "colSpan" | "rowSpan"> {
  if (!cell.isMerged || cell.master.address !== cell.address) return {};
  const range = cell.worksheet.model.merges.find((candidate) => candidate.split(":")[0] === cell.address);
  if (!range) return {};
  const [start, end] = range.split(":").map((address) => cell.worksheet.getCell(address));
  return { colSpan: Number(end.col) - Number(start.col) + 1, rowSpan: Number(end.row) - Number(start.row) + 1 };
}

function cellText(cell: ExcelJS.Cell): string {
  if ((typeof cell.value === "number" || cell.value instanceof Date) && cell.numFmt) {
    try {
      const value = cell.value instanceof Date
        ? (Date.UTC(cell.value.getFullYear(), cell.value.getMonth(), cell.value.getDate()) - Date.UTC(1899, 11, 30)) / 86400000
        : cell.value;
      return formatExcelValue(cell.numFmt, value);
    } catch {
      // Fall through to ExcelJS's safe textual representation for unsupported formats.
    }
  }
  return cell.text;
}

export async function parseSpreadsheetPreview(buffer: ArrayBuffer): Promise<PreviewSheet[]> {
  const { default: ExcelJS } = await import("exceljs");
  const workbook = new ExcelJS.Workbook();
  // ExcelJS accepts Uint8Array in browsers although its declaration is Node Buffer-only.
  await workbook.xlsx.load(new Uint8Array(buffer) as unknown as ExcelJS.Buffer);
  return workbook.worksheets.map((sheet) => {
    const maxRow = Math.min(sheet.actualRowCount || sheet.rowCount, MAX_ROWS);
    const maxCol = Math.min(sheet.actualColumnCount || sheet.columnCount, MAX_COLS);
    const rows: PreviewRow[] = [];
    for (let r = 1; r <= maxRow; r += 1) {
      const row = sheet.getRow(r); const cells: PreviewCell[] = [];
      for (let c = 1; c <= maxCol; c += 1) {
        const cell = row.getCell(c);
        if (cell.isMerged && cell.master.address !== cell.address) continue;
        cells.push({ key: cell.address, text: cellText(cell), style: cellStyle(cell), ...dimensions(cell) });
      }
      rows.push({ key: String(r), height: row.height ? row.height * 4 / 3 : undefined, cells });
    }
    const columnWidths = Array.from({ length: maxCol }, (_, i) => (sheet.getColumn(i + 1).width || 10) * 7);
    const rowTop = (row: number) => Array.from({ length: Math.max(0, row - 1) }, (_, i) => rows[i]?.height || 20).reduce((sum, value) => sum + value, 0);
    const colLeft = (col: number) => columnWidths.slice(0, Math.max(0, col)).reduce((sum, value) => sum + value, 0);
    const images: PreviewImage[] = [];
    for (const drawing of (sheet.getImages?.() || []) as any[]) {
      try {
        const media = (workbook as any).getImage(drawing.imageId);
        const range = drawing.range;
        const tl = range.tl; const br = range.br;
        const buffer = media?.buffer;
        if (!buffer || !tl || !br) continue;
        const bytes = buffer instanceof ArrayBuffer ? new Uint8Array(buffer) : new Uint8Array(buffer.buffer, buffer.byteOffset, buffer.byteLength);
        let binary = ""; bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
        const extension = String(media.extension || "png").toLowerCase();
        images.push({ key: `${sheet.name}-${drawing.imageId}-${tl.col}-${tl.row}`, src: `data:image/${extension === "jpg" ? "jpeg" : extension};base64,${btoa(binary)}`, left: colLeft(tl.col), top: rowTop(tl.row), width: Math.max(1, colLeft(br.col) - colLeft(tl.col)), height: Math.max(1, rowTop(br.row) - rowTop(tl.row)) });
      } catch { /* Unsupported image objects do not block table rendering. */ }
    }
    return { name: sheet.name, rows, columnWidths, images };
  });
}
