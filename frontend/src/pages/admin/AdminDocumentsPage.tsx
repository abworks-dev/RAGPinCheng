import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { CategoryTree, IndexJob, IndexedDocument } from "../../types";
import { formatAdminDate, formatBytes } from "./admin-formatters";
const NEW_CATEGORY_SENTINEL = "__new__";

const STATUS_LABELS: Record<string, string> = {
  pending: "排队中",
  uploading: "上传中",
  queued_mineru: "等待 MinerU",
  parsing: "解析中",
  chunking: "切块中",
  summarizing: "表格摘要中",
  embedding: "嵌入中",
  done: "已完成",
  failed: "失败",
};

const STATUS_HINTS: Record<string, string> = {
  uploading: "正在上传文件…",
  queued_mineru: "文件已提交，等待解析器开始处理…",
  parsing: "解析中…",
  chunking: "切块中…",
  summarizing: "正在为表格生成检索摘要…",
  embedding: "向量嵌入中…",
};

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-gray-100 text-gray-700",
  uploading: "bg-sky-100 text-sky-700",
  queued_mineru: "bg-violet-100 text-violet-700",
  parsing: "bg-blue-100 text-blue-700",
  chunking: "bg-blue-100 text-blue-700",
  summarizing: "bg-teal-100 text-teal-700",
  embedding: "bg-amber-100 text-amber-700",
  done: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
};

const ACTIVE_STATUSES = new Set(["pending", "uploading", "queued_mineru", "parsing", "chunking", "summarizing", "embedding"]);

function useElapsed(startTs: number | null | undefined): string {
  const [now, setNow] = useState(() => Date.now());
  const active = startTs != null;
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [active]);
  if (!startTs) return "";
  const sec = Math.floor((now - startTs * 1000) / 1000);
  if (sec < 60) return `${sec}s`;
  return `${Math.floor(sec / 60)}m${sec % 60}s`;
}



export function AdminDocumentsPage() {
  const [tree, setTree] = useState<CategoryTree | null>(null);
  const [documents, setDocuments] = useState<IndexedDocument[]>([]);
  const [jobs, setJobs] = useState<IndexJob[]>([]);
  const [loadingDocs, setLoadingDocs] = useState(true);
  const [loadingJobs, setLoadingJobs] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refreshAll = useCallback(async () => {
    try {
      const [t, docs, j] = await Promise.all([
        api.adminCategoryTree(),
        api.adminListIndexedDocuments(),
        api.adminListIndexJobs(100),
      ]);
      setTree(t);
      setDocuments(docs.documents);
      setJobs(j.jobs);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoadingDocs(false);
      setLoadingJobs(false);
    }
  }, []);

  useEffect(() => {
    refreshAll();
  }, [refreshAll]);

  // While anything is queued or running, poll the jobs list every 3s so the
  // admin sees status flip without manually refreshing. Stop polling when
  // everything settles.
  const hasActive = jobs.some(
    (j) => j.status !== "done" && j.status !== "failed",
  );
  useEffect(() => {
    if (!hasActive) return;
    const t = window.setInterval(async () => {
      try {
        const { jobs: latest } = await api.adminListIndexJobs(100);
        setJobs(latest);
        // Once a job finishes, refresh documents too.
        if (latest.some((j) => j.status === "done")) {
          const docs = await api.adminListIndexedDocuments();
          setDocuments(docs.documents);
        }
      } catch {
        /* ignore — next tick retries */
      }
    }, 3000);
    return () => window.clearInterval(t);
  }, [hasActive]);

  return (
    <div className="space-y-6">
      {error && <div className="text-sm text-red-600">{error}</div>}
      <UploadCard
        tree={tree}
        onUploaded={() => refreshAll()}
      />
      <DocumentsCard
        documents={documents}
        loading={loadingDocs}
        onChange={() => refreshAll()}
      />
      <JobsCard
        jobs={jobs}
        loading={loadingJobs}
        onChange={() => refreshAll()}
      />
    </div>
  );
}


function UploadCard({
  tree,
  onUploaded,
}: {
  tree: CategoryTree | null;
  onUploaded: () => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [pickedCategory, setPickedCategory] = useState<string>("");
  const [newCategory, setNewCategory] = useState<string>("");
  const [pickedSub, setPickedSub] = useState<string>("");
  const [newSub, setNewSub] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{
    accepted: number;
    skipped: { filename: string; reason: string }[];
  } | null>(null);

  const categoryNames = useMemo(
    () => (tree ? tree.categories.map((c) => c.name) : []),
    [tree],
  );

  // Default the dropdown to the first known category once they load.
  useEffect(() => {
    if (!pickedCategory && categoryNames.length > 0) {
      setPickedCategory(categoryNames[0]);
    }
  }, [categoryNames, pickedCategory]);

  const effectiveCategory =
    pickedCategory === NEW_CATEGORY_SENTINEL ? newCategory.trim() : pickedCategory;

  // Look up the node for the currently-selected (existing) category.
  // New categories typed by the admin are treated as flat — admins who need
  // a two-level new category can just type "客户标准" which already exists.
  const currentNode = useMemo(() => {
    if (!tree) return null;
    return tree.categories.find((c) => c.name === effectiveCategory) || null;
  }, [tree, effectiveCategory]);

  const needsSubcategory = !!currentNode?.two_level;
  const existingSubs = currentNode?.subcategories || [];

  // Reset subcategory selection when the parent category changes so the
  // dropdown defaults sensibly (first existing sub, or "+ new" if empty).
  useEffect(() => {
    if (!needsSubcategory) {
      setPickedSub("");
      setNewSub("");
      return;
    }
    if (existingSubs.length > 0) {
      setPickedSub(existingSubs[0]);
    } else {
      setPickedSub(NEW_CATEGORY_SENTINEL);
    }
    setNewSub("");
  }, [effectiveCategory, needsSubcategory, existingSubs.join("|")]);

  const effectiveSub = needsSubcategory
    ? pickedSub === NEW_CATEGORY_SENTINEL ? newSub.trim() : pickedSub
    : "";

  const canSubmit =
    files.length > 0 &&
    !!effectiveCategory &&
    (!needsSubcategory || !!effectiveSub);

  async function submit() {
    if (!canSubmit) return;
    setSubmitting(true);
    setResult(null);
    try {
      const r = await api.adminUploadDocuments(
        files,
        effectiveCategory,
        effectiveSub || undefined,
      );
      setResult({ accepted: r.accepted.length, skipped: r.skipped });
      setFiles([]);
      const input = document.getElementById("corpus-upload-input") as HTMLInputElement | null;
      if (input) input.value = "";
      onUploaded();
    } catch (e: any) {
      setResult({ accepted: 0, skipped: [{ filename: "(upload)", reason: e?.message || String(e) }] });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-panel p-4">
      <h2 className="font-semibold mb-3">上传资料</h2>
      <p className="text-xs text-muted mb-3">
        支持 <code>.pdf</code>（自动经 MinerU 解析）、<code>.docx</code>（自动经 Docling 解析）、
        <code>.xlsx</code>、<code>.pptx</code> 与 <code>.md</code>。
        在「教学视频」分类下上传的 <code>.md</code> 会按转写格式（说话人 + 时间戳）处理，
        其它分类下则作为普通 Markdown 文档（按标题切分）处理。
        可一次选择多个文件，会依次排队；处理过程中可继续聊天，但响应可能变慢。
      </p>
      <div className="flex flex-col gap-3">
        <div>
          <label className="block text-sm mb-1">分类</label>
          <div className="flex items-center gap-2">
            <select
              value={pickedCategory}
              onChange={(e) => setPickedCategory(e.target.value)}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm bg-bg"
            >
              {categoryNames.length === 0 && <option value="">（暂无现有分类）</option>}
              {categoryNames.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
              <option value={NEW_CATEGORY_SENTINEL}>＋ 新建分类…</option>
            </select>
            {pickedCategory === NEW_CATEGORY_SENTINEL && (
              <input
                type="text"
                value={newCategory}
                onChange={(e) => setNewCategory(e.target.value)}
                placeholder="新分类名（如：行业规范）"
                className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm bg-bg flex-1"
                autoFocus
              />
            )}
          </div>
        </div>
        {needsSubcategory && (
          <div>
            <label className="block text-sm mb-1">
              {effectiveCategory === "客户标准" ? "客户" : "公司"}
              <span className="text-xs text-muted ml-2">
                （「{effectiveCategory}」按 {effectiveCategory === "客户标准" ? "客户" : "公司"} 分组）
              </span>
            </label>
            <div className="flex items-center gap-2">
              <select
                value={pickedSub}
                onChange={(e) => setPickedSub(e.target.value)}
                className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm bg-bg"
              >
                {existingSubs.length === 0 && (
                  <option value={NEW_CATEGORY_SENTINEL}>
                    （暂无；请新建）
                  </option>
                )}
                {existingSubs.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
                {existingSubs.length > 0 && (
                  <option value={NEW_CATEGORY_SENTINEL}>＋ 新建…</option>
                )}
              </select>
              {pickedSub === NEW_CATEGORY_SENTINEL && (
                <input
                  type="text"
                  value={newSub}
                  onChange={(e) => setNewSub(e.target.value)}
                  placeholder={
                    effectiveCategory === "客户标准"
                      ? "新客户名（如：C客户标准）"
                      : "新公司名"
                  }
                  className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm bg-bg flex-1"
                  autoFocus
                />
              )}
            </div>
          </div>
        )}
        <div>
          <label className="block text-sm mb-1">文件</label>
          <input
            id="corpus-upload-input"
            type="file"
            multiple
            accept=".pdf,.md,.docx,.xlsx,.pptx"
            onChange={(e) => setFiles(Array.from(e.target.files || []))}
            className="text-sm"
          />
          {files.length > 0 && (
            <ul className="mt-2 text-xs text-muted space-y-0.5">
              {files.map((f) => (
                <li key={f.name}>
                  {f.name} <span className="ml-1">· {formatBytes(f.size)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <button
            type="button"
            onClick={submit}
            disabled={submitting || !canSubmit}
            className="rounded-lg bg-accent text-white px-4 py-1.5 text-sm hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? "上传中…" : `上传 ${files.length} 个文件`}
          </button>
        </div>
        {result && (
          <div className="text-sm">
            {result.accepted > 0 && (
              <div className="text-green-700">
                已加入队列 {result.accepted} 个文件，可在下方“索引任务”查看进度。
              </div>
            )}
            {result.skipped.length > 0 && (
              <div className="text-red-600 mt-1">
                以下文件未受理：
                <ul className="list-disc list-inside">
                  {result.skipped.map((s, i) => (
                    <li key={i}>{s.filename}：{s.reason}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}


function DocumentsCard({
  documents,
  loading,
  onChange,
}: {
  documents: IndexedDocument[];
  loading: boolean;
  onChange: () => void;
}) {
  const [filter, setFilter] = useState("");

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return documents;
    return documents.filter(
      (d) =>
        d.doc_title.toLowerCase().includes(q) ||
        d.category.toLowerCase().includes(q),
    );
  }, [documents, filter]);

  async function onDelete(d: IndexedDocument) {
    const ok = confirm(
      `从索引中移除「${d.doc_title}」？\n` +
        `这将删除该资料的 ${d.parent_count} 个父段落及其所有子块。`,
    );
    if (!ok) return;
    const alsoFile = confirm("同时从磁盘删除源文件？（取消 = 仅清除索引，保留文件）");
    try {
      await api.adminDeleteIndexedDocument(d.source_path, alsoFile);
      onChange();
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-panel p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="font-semibold">已索引资料</h2>
        <div className="flex items-center gap-2">
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="按标题或分类筛选…"
            className="w-64 rounded-lg border border-gray-300 px-3 py-1 text-sm bg-bg"
          />
          <span className="text-xs text-muted">
            {filter ? `${visible.length} / ${documents.length}` : `${documents.length} 个文档`}
          </span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-muted">
            <tr>
              <th className="text-left px-2 py-1">标题</th>
              <th className="text-left px-2 py-1">分类</th>
              <th className="text-left px-2 py-1">类型</th>
              <th className="text-right px-2 py-1">父段落</th>
              <th className="text-left px-2 py-1">操作</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={5} className="px-2 py-3 text-muted">加载中…</td></tr>
            )}
            {!loading && visible.length === 0 && (
              <tr>
                <td colSpan={5} className="px-2 py-3 text-muted">
                  {filter ? `没有匹配 “${filter}” 的资料` : "（暂无已索引资料 — 上传 PDF 或转写以开始）"}
                </td>
              </tr>
            )}
            {visible.map((d) => (
              <tr key={d.source_path} className="border-t border-gray-100 dark:border-gray-800">
                <td className="px-2 py-1.5 max-w-md truncate" title={d.doc_title}>
                  {d.doc_title}
                </td>
                <td className="px-2 py-1.5">{d.category}</td>
                <td className="px-2 py-1.5 text-muted">
                  {d.doc_type === "transcript"
                    ? "教学视频转写"
                    : d.doc_type === "docx"
                      ? "Word 文档"
                      : d.doc_type === "xlsx"
                        ? "Excel 表格"
                        : d.doc_type === "pptx"
                          ? "PPT 演示"
                          : d.source_path.toLowerCase().endsWith(".md")
                            ? "Markdown 文档"
                            : "PDF"}
                </td>
                <td className="px-2 py-1.5 text-right">{d.parent_count}</td>
                <td className="px-2 py-1.5">
                  <button
                    type="button"
                    onClick={() => onDelete(d)}
                    className="text-xs text-red-600 hover:underline"
                  >
                    删除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


function JobStatusCell({ job: j }: { job: IndexJob }) {
  const isActive = ACTIVE_STATUSES.has(j.status);
  const elapsed = useElapsed(isActive ? j.started_at ?? j.created_at : null);
  const hint = isActive ? STATUS_HINTS[j.status] : null;
  return (
    <>
      <span
        className={
          "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] " +
          (STATUS_COLORS[j.status] || "bg-gray-100 text-gray-700") +
          (isActive ? " animate-pulse" : "")
        }
      >
        {isActive && (
          <svg className="w-2.5 h-2.5 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z" />
          </svg>
        )}
        {STATUS_LABELS[j.status] || j.status}
        {elapsed && <span className="opacity-70">{elapsed}</span>}
      </span>
      {hint && (
        <div className="text-[11px] text-muted mt-0.5">{hint}</div>
      )}
      {j.error && (
        <div
          className="text-[11px] text-red-600 mt-1 max-w-xs whitespace-pre-wrap"
          title={j.error}
        >
          {j.error.length > 200 ? j.error.slice(0, 200) + "…" : j.error}
        </div>
      )}
    </>
  );
}

function JobsCard({
  jobs,
  loading,
  onChange,
}: {
  jobs: IndexJob[];
  loading: boolean;
  onChange: () => void;
}) {
  async function onRetry(j: IndexJob) {
    try {
      await api.adminRetryIndexJob(j.id);
      onChange();
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }
  async function onDelete(j: IndexJob) {
    if (!confirm(`删除该任务记录？（不影响已索引的内容）`)) return;
    try {
      await api.adminDeleteIndexJob(j.id);
      onChange();
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-panel p-4">
      <h2 className="font-semibold mb-3">索引任务</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-muted">
            <tr>
              <th className="text-left px-2 py-1">文件</th>
              <th className="text-left px-2 py-1">分类</th>
              <th className="text-left px-2 py-1">状态</th>
              <th className="text-left px-2 py-1">上传者</th>
              <th className="text-left px-2 py-1">时间</th>
              <th className="text-right px-2 py-1">规模</th>
              <th className="text-left px-2 py-1">操作</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={7} className="px-2 py-3 text-muted">加载中…</td></tr>
            )}
            {!loading && jobs.length === 0 && (
              <tr><td colSpan={7} className="px-2 py-3 text-muted">（暂无任务）</td></tr>
            )}
            {jobs.map((j) => (
              <tr key={j.id} className="border-t border-gray-100 dark:border-gray-800 align-top">
                <td className="px-2 py-1.5 max-w-xs truncate" title={j.filename}>
                  {j.filename}
                  <div className="text-[11px] text-muted">{formatBytes(j.file_size)}</div>
                </td>
                <td className="px-2 py-1.5">{j.category}</td>
                <td className="px-2 py-1.5">
                  <JobStatusCell job={j} />
                </td>
                <td className="px-2 py-1.5 text-muted">
                  {j.real_name || "—"}
                  {j.employee_id && (
                    <div className="text-[11px]">{j.employee_id}</div>
                  )}
                </td>
                <td className="px-2 py-1.5 text-muted text-[11px]">
                  <div>提交 {formatAdminDate(j.created_at)}</div>
                  {j.started_at && !j.finished_at && (
                    <div>开始 {formatAdminDate(j.started_at)}</div>
                  )}
                  {j.finished_at && <div>完成 {formatAdminDate(j.finished_at)}</div>}
                </td>
                <td className="px-2 py-1.5 text-right text-[11px] text-muted">
                  {j.status === "done" && j.parents != null && j.children != null
                    ? `${j.parents} 父 / ${j.children} 子`
                    : "—"}
                </td>
                <td className="px-2 py-1.5 space-x-2 whitespace-nowrap">
                  {(j.status === "failed" || j.status === "done") && (
                    <button
                      type="button"
                      onClick={() => onRetry(j)}
                      className="text-xs text-accent hover:underline"
                    >
                      重试
                    </button>
                  )}
                  {(j.status === "done" || j.status === "failed") && (
                    <button
                      type="button"
                      onClick={() => onDelete(j)}
                      className="text-xs text-muted hover:text-red-600"
                    >
                      删除记录
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
