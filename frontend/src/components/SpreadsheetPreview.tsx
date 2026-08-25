import { useEffect, useRef, useState } from "react";
import { parseSpreadsheetPreview, type PreviewSheet } from "../lib/xlsx-preview";

export function SpreadsheetPreview({ sheetName, cellRange, parentId, onLoad, onError, zoom = 1 }: {
  parentId: string; sheetName?: string | null; cellRange?: string | null;
  onLoad?: () => void; onError?: (err: string) => void; zoom?: number;
}) {
  const [sheets, setSheets] = useState<PreviewSheet[]>([]);
  const [activeSheet, setActiveSheet] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const onLoadRef = useRef(onLoad);
  const onErrorRef = useRef(onError);
  const gridRef = useRef<HTMLDivElement>(null);
  onLoadRef.current = onLoad;
  onErrorRef.current = onError;

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError(false); setSheets([]); setActiveSheet(0);
    void (async () => {
      try {
        const response = await fetch(`/api/source/${parentId}/raw`, { credentials: "include" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const parsed = await parseSpreadsheetPreview(await response.arrayBuffer());
        if (cancelled) return;
        setSheets(parsed);
        if (sheetName) {
          const target = parsed.findIndex((sheet) => sheet.name === sheetName);
          if (target >= 0) setActiveSheet(target);
        }
        setLoading(false); onLoadRef.current?.();
      } catch {
        if (!cancelled) { setError(true); setLoading(false); onErrorRef.current?.("XLSX preview unavailable"); }
      }
    })();
    return () => { cancelled = true; };
  }, [parentId, sheetName]);

  const rangeBounds = (() => {
    const match = cellRange?.toUpperCase().match(/^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$/);
    if (!match) return null;
    const column = (letters: string) => Array.from(letters).reduce((value, letter) => value * 26 + letter.charCodeAt(0) - 64, 0);
    return { startCol: column(match[1]), startRow: Number(match[2]), endCol: column(match[3] || match[1]), endRow: Number(match[4] || match[2]) };
  })();

  useEffect(() => {
    if (!rangeBounds || !gridRef.current) return;
    requestAnimationFrame(() => gridRef.current?.querySelector<HTMLElement>(`[data-cell-address="${cellRange?.split(":")[0].toUpperCase()}"]`)?.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" }));
  }, [activeSheet, cellRange, sheets]);

  if (error) return <div className="flex h-full items-center justify-center p-4 text-sm text-red-600">暂时无法预览此 Excel 表格，请确认源文件仍然存在且格式有效。</div>;
  if (loading) return <div className="flex min-h-full items-center justify-center bg-secondary text-sm text-muted">加载 XLSX…</div>;
  if (!sheets.length) return <div className="flex h-full items-center justify-center text-sm text-muted">此文件没有工作表</div>;
  const current = sheets[activeSheet];

  const isLocatedCell = (address: string, colSpan = 1, rowSpan = 1) => {
    if (!rangeBounds) return false;
    const match = address.match(/^([A-Z]+)(\d+)$/);
    if (!match) return false;
    const col = Array.from(match[1]).reduce((value, letter) => value * 26 + letter.charCodeAt(0) - 64, 0);
    const row = Number(match[2]);
    return col <= rangeBounds.endCol && col + colSpan - 1 >= rangeBounds.startCol && row <= rangeBounds.endRow && row + rowSpan - 1 >= rangeBounds.startRow;
  };

  return <div className="flex h-full flex-col bg-secondary">
    <div className="flex shrink-0 items-center overflow-x-auto border-b border-border bg-background px-2">
      <div className="flex items-center overflow-x-auto">
      {sheets.map((sheet, index) => <button key={sheet.name} type="button" onClick={() => setActiveSheet(index)} className={`border-r border-gray-200 px-3 py-2 text-xs whitespace-nowrap dark:border-gray-700 ${index === activeSheet ? "bg-white font-semibold text-accent dark:bg-gray-900" : "text-muted hover:bg-gray-100 dark:hover:bg-gray-700"}`}>{sheet.name}</button>)}
      </div>
    </div>
    <div ref={gridRef} className="min-h-full overflow-auto p-4">
      {current.rows.length ? <div className="relative origin-top-left will-change-transform" style={{ transform: `scale(${zoom})`, transformOrigin: "top left", width: `${100 / zoom}%` }}><table className="border-collapse bg-background text-xs shadow-sm" aria-label={current.name}>
        <colgroup>{current.columnWidths.map((width, index) => <col key={index} style={{ width, minWidth: width }} />)}</colgroup>
        <tbody>{current.rows.map((row) => <tr key={row.key} style={{ height: row.height }}>{row.cells.map((cell) => <td key={cell.key} data-cell-address={cell.key} colSpan={cell.colSpan} rowSpan={cell.rowSpan} className={`overflow-hidden border px-1.5 py-1 ${isLocatedCell(cell.key, cell.colSpan, cell.rowSpan) ? "border-yellow-600 bg-yellow-100 outline outline-1 outline-yellow-500 dark:bg-yellow-900/40" : "border-gray-200 dark:border-gray-700"}`} style={cell.style}>{cell.text}</td>)}</tr>)}</tbody>
      </table>{current.images.map((image) => <img key={image.key} src={image.src} alt="" className="pointer-events-none absolute object-contain" style={{ left: image.left, top: image.top, width: image.width, height: image.height }} />)}</div> : <div className="flex h-full items-center justify-center text-sm text-muted">此工作表没有数据</div>}
    </div>
  </div>;
}
