import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { AdminFeedbackEntry } from "../../types";
export function AdminFeedbackPage() {
  const [entries, setEntries] = useState<AdminFeedbackEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const r = await api.adminFeedback(200);
        setEntries(r.entries);
        setTotal(r.total);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <div className="text-sm text-muted">加载中…</div>;
  if (entries.length === 0) {
    return <div className="text-sm text-muted">暂无反馈记录。</div>;
  }

  return (
    <div className="space-y-3">
      <div className="text-xs text-muted">
        共 {total} 条；显示最近 {entries.length} 条。
      </div>
      {entries.map((e, i) => (
        <div key={i} className="rounded-lg border border-gray-200 bg-panel p-3 text-sm">
          <div className="flex items-center gap-2 text-xs text-muted">
            <span>{e.ts || "—"}</span>
            <span className="px-1.5 rounded bg-gray-100 dark:bg-gray-800">{e.kind || "?"}</span>
            {e.rating && (
              <span
                className={
                  e.rating === "down"
                    ? "px-1.5 rounded bg-red-100 text-red-700"
                    : "px-1.5 rounded bg-green-100 text-green-700"
                }
              >
                {e.rating}
              </span>
            )}
          </div>
          {e.query && (
            <div className="mt-2">
              <div className="text-[11px] text-muted">问题</div>
              <div>{e.query}</div>
            </div>
          )}
          {e.note && (
            <div className="mt-2">
              <div className="text-[11px] text-muted">用户反馈</div>
              <div className="whitespace-pre-wrap">{e.note}</div>
            </div>
          )}
          {e.doc_title && (
            <div className="mt-2 text-xs text-muted">
              来源：[{e.doc_title}] {e.section_path || ""} {e.start_time ? `@${e.start_time}` : ""}
            </div>
          )}
          {e.answer_text && (
            <details className="mt-2">
              <summary className="text-[11px] text-muted cursor-pointer">查看回答</summary>
              <div className="mt-1 whitespace-pre-wrap text-xs text-gray-700 dark:text-gray-300">
                {e.answer_text}
              </div>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}
