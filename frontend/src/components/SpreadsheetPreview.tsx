import { useEffect, useRef, useState } from "react";
import * as XLSX from "xlsx";

interface SheetData {
  name: string;
  rows: string[][];
  cols: number;
}

const MAX_ROWS = 5000;
const MAX_COLS = 100;

export function SpreadsheetPreview({
  sheetName,
  cellRange,
  parentId,
  onLoad,
  onError,
}: {
  parentId: string;
  onLoad?: () => void;
  sheetName?: string | null;
  cellRange?: string | null;
  onError?: (err: string) => void;
}) {
  const [sheets, setSheets] = useState<SheetData[]>([]);
  const [activeSheet, setActiveSheet] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const resp = await fetch(`/api/source/${parentId}/raw`, {
          credentials: "include",
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        const buf = await resp.arrayBuffer();
        const wb = XLSX.read(buf, { type: "array" });

        const allSheets: SheetData[] = [];
        for (const name of wb.SheetNames) {
          const ws = wb.Sheets[name];
          const json = XLSX.utils.sheet_to_json<string[]>(ws, {
            header: 1,
            defval: "",
            blankrows: false,
          });

          if (json.length === 0) continue;

          const truncated = json.slice(0, MAX_ROWS).map(
            (row) => row.slice(0, MAX_COLS).map((c) => String(c ?? ""))
          );
          const maxCols = Math.max(...truncated.map((r) => r.length), 0);

          allSheets.push({
            name,
            rows: truncated,
            cols: maxCols,
          });
        }

        if (!cancelled) {
          setSheets(allSheets);
          setLoading(false);
          onLoad?.();

          // Navigate to target sheet if provided
          if (sheetName) {
            var idx = allSheets.findIndex(function(s) { return s.name === sheetName; });
            if (idx >= 0) setActiveSheet(idx);
          }
          // Scroll to target row if cellRange is provided
          if (cellRange) {
            var match = cellRange.match(/d+/);
            if (match) {
              var targetRow = parseInt(match[0], 10);
              setTimeout(function() {
                var rows = document.querySelectorAll(".\" + scrollRef.current.className.split(" ").join(".") + " tbody tr");
                if (rows[targetRow]) rows[targetRow].scrollIntoView({ behavior: "smooth", block: "center" });
              }, 100);
            }
          }
        }
      } catch (e: any) {
        if (!cancelled) {
          const msg = e?.message || String(e);
          setError(msg);
          setLoading(false);
          onError?.(msg);
        }
      }
    }

    load();
    return () => { cancelled = true; };
  }, [parentId, onLoad, onError]);

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-red-600 p-4">
        XLSX 加载失败：{error}
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-muted">
        加载 XLSX…
      </div>
    );
  }

  if (sheets.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-muted">
        此文件没有数据
      </div>
    );
  }

  const current = sheets[activeSheet];
  const truncated = current && current.rows.length >= MAX_ROWS;

  return (
    <div className="h-full flex flex-col">
      {/* Sheet tabs */}
      <div className="flex items-center border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 shrink-0 overflow-x-auto">
        {sheets.map((s, i) => (
          <button
            key={s.name}
            type="button"
            onClick={() => setActiveSheet(i)}
            className={
              "px-3 py-2 text-xs whitespace-nowrap border-r border-gray-200 dark:border-gray-700 " +
              (i === activeSheet
                ? "bg-white dark:bg-gray-900 font-semibold text-accent"
                : "text-muted hover:bg-gray-100 dark:hover:bg-gray-700")
            }
          >
            {s.name}
          </button>
        ))}
      </div>

      {/* Truncation warning */}
      {truncated && (
        <div className="px-3 py-1.5 text-xs text-amber-600 bg-amber-50 dark:bg-amber-900/20 border-b border-gray-200 shrink-0">
          仅显示前 {MAX_ROWS} 行，完整文件可下载查看
        </div>
      )}

      {/* Table */}
      <div ref={scrollRef} className="flex-1 overflow-auto">
        {current && (
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-800 sticky top-0 z-10">
                {Array.from({ length: current.cols }).map((_, ci) => (
                  <th
                    key={ci}
                    className="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-left font-medium text-muted whitespace-nowrap min-w-[80px]"
                  >
                    {current.rows[0]?.[ci] || ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {current.rows.slice(1).map((row, ri) => (
                <tr
                  key={ri}
                  className={ri % 2 === 1 ? "bg-gray-50/50 dark:bg-gray-800/30" : ""}
                >
                  {Array.from({ length: current.cols }).map((_, ci) => (
                    <td
                      key={ci}
                      className="border border-gray-200 dark:border-gray-700 px-2 py-1 whitespace-nowrap"
                    >
                      {row[ci] || ""}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}