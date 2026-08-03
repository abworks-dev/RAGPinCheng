import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import type { CategoryTree, IndexJob, IndexedDocument } from "../../types";
import { formatAdminDate, formatBytes } from "./admin-formatters";

const NEW_CATEGORY_SENTINEL = "__new__";
const ACTIVE_STATUSES = new Set([
  "pending",
  "uploading",
  "queued_mineru",
  "parsing",
  "chunking",
  "summarizing",
  "embedding",
]);

type StatusVariant = "secondary" | "success" | "warning" | "destructive" | "info";

const STATUS_META: Record<string, { label: string; hint?: string; variant: StatusVariant }> = {
  pending: { label: "排队中", hint: "任务正在等待处理…", variant: "secondary" },
  uploading: { label: "上传中", hint: "正在上传文件…", variant: "info" },
  queued_mineru: { label: "等待 MinerU", hint: "文件已提交，等待解析器开始处理…", variant: "warning" },
  parsing: { label: "解析中", hint: "正在解析文档内容…", variant: "info" },
  chunking: { label: "切块中", hint: "正在生成可检索的内容块…", variant: "info" },
  summarizing: { label: "表格摘要中", hint: "正在为表格生成检索摘要…", variant: "warning" },
  embedding: { label: "嵌入中", hint: "正在写入向量索引…", variant: "warning" },
  done: { label: "已完成", variant: "success" },
  failed: { label: "失败", variant: "destructive" },
};

const selectClassName =
  "h-control-md w-full rounded-ui-md border border-input bg-background px-3 text-ui-sm text-foreground shadow-sm outline-none transition-colors duration-normal focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50";

function useElapsed(startTs: number | null | undefined): string {
  const [now, setNow] = useState(() => Date.now());
  const active = startTs != null;

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);

  if (!startTs) return "";
  const seconds = Math.max(0, Math.floor((now - startTs * 1000) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m${seconds % 60}s`;
}

function documentTypeLabel(document: IndexedDocument): string {
  if (document.doc_type === "transcript") return "教学视频转写";
  if (document.doc_type === "docx") return "Word 文档";
  if (document.doc_type === "xlsx") return "Excel 表格";
  if (document.doc_type === "pptx") return "PPT 演示";
  if (document.source_path.toLowerCase().endsWith(".md")) return "Markdown 文档";
  return "PDF";
}

export function AdminDocumentsPage() {
  const [tree, setTree] = useState<CategoryTree | null>(null);
  const [documents, setDocuments] = useState<IndexedDocument[]>([]);
  const [jobs, setJobs] = useState<IndexJob[]>([]);
  const [loadingDocs, setLoadingDocs] = useState(true);
  const [loadingJobs, setLoadingJobs] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refreshAll = useCallback(async () => {
    setLoadingDocs(true);
    setLoadingJobs(true);
    setError(null);
    try {
      const [categoryTree, indexedDocuments, indexJobs] = await Promise.all([
        api.adminCategoryTree(),
        api.adminListIndexedDocuments(),
        api.adminListIndexJobs(100),
      ]);
      setTree(categoryTree);
      setDocuments(indexedDocuments.documents);
      setJobs(indexJobs.jobs);
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

  const hasActive = jobs.some((job) => job.status !== "done" && job.status !== "failed");

  useEffect(() => {
    if (!hasActive) return;
    const timer = window.setInterval(async () => {
      try {
        const { jobs: latest } = await api.adminListIndexJobs(100);
        setJobs(latest);
        if (latest.some((job) => job.status === "done")) {
          const indexedDocuments = await api.adminListIndexedDocuments();
          setDocuments(indexedDocuments.documents);
        }
      } catch {
        // Polling is best-effort. The next tick retries without replacing visible data.
      }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [hasActive]);

  return (
    <section className="space-y-6" aria-labelledby="admin-documents-title">
      <header>
        <p className="text-ui-xs font-medium uppercase tracking-[0.14em] text-primary">知识库维护</p>
        <h1 id="admin-documents-title" className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">
          资料管理与索引任务
        </h1>
        <p className="mt-1 max-w-3xl text-ui-sm text-muted-foreground">
          上传并分类知识库资料，查看已索引内容，并跟踪解析、切块和向量写入进度。
        </p>
      </header>

      {error && (
        <ErrorState
          title="资料与索引数据加载失败"
          description={error}
          action={
            <Button variant="outline" size="sm" onClick={refreshAll}>
              重新加载
            </Button>
          }
        />
      )}

      <UploadCard tree={tree} onUploaded={refreshAll} />
      <DocumentsCard documents={documents} loading={loadingDocs} onChange={refreshAll} />
      <JobsCard jobs={jobs} loading={loadingJobs} onChange={refreshAll} />
    </section>
  );
}

function UploadCard({
  tree,
  onUploaded,
}: {
  tree: CategoryTree | null;
  onUploaded: () => Promise<void> | void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [pickedCategory, setPickedCategory] = useState("");
  const [newCategory, setNewCategory] = useState("");
  const [pickedSub, setPickedSub] = useState("");
  const [newSub, setNewSub] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{
    accepted: number;
    skipped: { filename: string; reason: string }[];
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const categoryNames = useMemo(
    () => (tree ? tree.categories.map((category) => category.name) : []),
    [tree],
  );

  useEffect(() => {
    if (!pickedCategory && categoryNames.length > 0) {
      setPickedCategory(categoryNames[0]);
    }
  }, [categoryNames, pickedCategory]);

  const effectiveCategory =
    pickedCategory === NEW_CATEGORY_SENTINEL ? newCategory.trim() : pickedCategory;

  const currentNode = useMemo(() => {
    if (!tree) return null;
    return tree.categories.find((category) => category.name === effectiveCategory) || null;
  }, [tree, effectiveCategory]);

  const needsSubcategory = Boolean(currentNode?.two_level);
  const existingSubs = currentNode?.subcategories || [];

  useEffect(() => {
    if (!needsSubcategory) {
      setPickedSub("");
      setNewSub("");
      return;
    }
    setPickedSub(existingSubs.length > 0 ? existingSubs[0] : NEW_CATEGORY_SENTINEL);
    setNewSub("");
  }, [effectiveCategory, needsSubcategory, existingSubs.join("|")]);

  const effectiveSub = needsSubcategory
    ? pickedSub === NEW_CATEGORY_SENTINEL
      ? newSub.trim()
      : pickedSub
    : "";

  const canSubmit =
    files.length > 0 &&
    Boolean(effectiveCategory) &&
    (!needsSubcategory || Boolean(effectiveSub));
  const totalSize = files.reduce((sum, file) => sum + file.size, 0);

  async function submit() {
    if (!canSubmit) return;
    setSubmitting(true);
    setResult(null);
    try {
      const response = await api.adminUploadDocuments(
        files,
        effectiveCategory,
        effectiveSub || undefined,
      );
      setResult({ accepted: response.accepted.length, skipped: response.skipped });
      setFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = "";
      await onUploaded();
    } catch (e: any) {
      setResult({
        accepted: 0,
        skipped: [{ filename: "(upload)", reason: e?.message || String(e) }],
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="shadow-surface">
      <CardHeader className="p-5 pb-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <CardTitle id="document-upload-title" className="text-ui-lg">
              上传资料
            </CardTitle>
            <CardDescription className="mt-1 max-w-3xl leading-relaxed">
              支持 PDF、Word、Excel、PPT 和 Markdown，可一次选择多个文件并依次排队处理。
            </CardDescription>
          </div>
          <Badge variant="outline" className="shrink-0">
            PDF · DOCX · XLSX · PPTX · MD
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-5 px-5 pb-5 pt-0">
        <Alert variant="info">
          <AlertTitle>分类和解析说明</AlertTitle>
          <AlertDescription className="leading-relaxed">
            PDF 会经 MinerU 解析，DOCX 会经 Docling 解析；“教学视频”分类下的 Markdown 按说话人和时间戳转写处理，
            其它 Markdown 按标题切分。处理期间仍可继续问答，但响应可能变慢。
          </AlertDescription>
        </Alert>

        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <label htmlFor="document-category" className="mb-1.5 block text-ui-sm font-medium text-foreground">
              分类
            </label>
            <select
              id="document-category"
              value={pickedCategory}
              onChange={(event) => setPickedCategory(event.target.value)}
              disabled={submitting}
              className={selectClassName}
            >
              {categoryNames.length === 0 && <option value="">（暂无现有分类）</option>}
              {categoryNames.map((category) => (
                <option key={category} value={category}>{category}</option>
              ))}
              <option value={NEW_CATEGORY_SENTINEL}>＋ 新建分类…</option>
            </select>
          </div>

          {pickedCategory === NEW_CATEGORY_SENTINEL && (
            <div>
              <label htmlFor="document-new-category" className="mb-1.5 block text-ui-sm font-medium text-foreground">
                新分类名称
              </label>
              <Input
                id="document-new-category"
                value={newCategory}
                onChange={(event) => setNewCategory(event.target.value)}
                placeholder="例如：行业规范"
                disabled={submitting}
                autoFocus
              />
            </div>
          )}

          {needsSubcategory && (
            <>
              <div>
                <label htmlFor="document-subcategory" className="mb-1.5 block text-ui-sm font-medium text-foreground">
                  {effectiveCategory === "客户标准" ? "客户" : "公司"}
                </label>
                <select
                  id="document-subcategory"
                  value={pickedSub}
                  onChange={(event) => setPickedSub(event.target.value)}
                  disabled={submitting}
                  className={selectClassName}
                >
                  {existingSubs.length === 0 && (
                    <option value={NEW_CATEGORY_SENTINEL}>（暂无；请新建）</option>
                  )}
                  {existingSubs.map((subcategory) => (
                    <option key={subcategory} value={subcategory}>{subcategory}</option>
                  ))}
                  {existingSubs.length > 0 && (
                    <option value={NEW_CATEGORY_SENTINEL}>＋ 新建…</option>
                  )}
                </select>
                <p className="mt-1 text-ui-xs text-muted-foreground">
                  “{effectiveCategory}”会按{effectiveCategory === "客户标准" ? "客户" : "公司"}分组。
                </p>
              </div>

              {pickedSub === NEW_CATEGORY_SENTINEL && (
                <div>
                  <label htmlFor="document-new-subcategory" className="mb-1.5 block text-ui-sm font-medium text-foreground">
                    新{effectiveCategory === "客户标准" ? "客户" : "公司"}名称
                  </label>
                  <Input
                    id="document-new-subcategory"
                    value={newSub}
                    onChange={(event) => setNewSub(event.target.value)}
                    placeholder={effectiveCategory === "客户标准" ? "例如：C 客户标准" : "输入新公司名"}
                    disabled={submitting}
                    autoFocus
                  />
                </div>
              )}
            </>
          )}
        </div>

        <div className="rounded-ui-xl border border-border bg-surface-muted/40 p-4">
          <label htmlFor="corpus-upload-input" className="block text-ui-sm font-medium text-foreground">
            文件
          </label>
          <p className="mt-1 text-ui-xs text-muted-foreground">
            可多选；允许扩展名：.pdf、.md、.docx、.xlsx、.pptx。
          </p>
          <Input
            ref={fileInputRef}
            id="corpus-upload-input"
            type="file"
            multiple
            accept=".pdf,.md,.docx,.xlsx,.pptx"
            disabled={submitting}
            onChange={(event) => setFiles(Array.from(event.target.files || []))}
            className="mt-3 h-auto min-h-control-md cursor-pointer py-1.5 file:mr-3 file:rounded-ui-sm file:border-0 file:bg-secondary file:px-3 file:py-1.5 file:text-ui-xs file:font-medium file:text-secondary-foreground hover:file:bg-secondary/80"
          />
          <div className="mt-3" aria-live="polite">
            {files.length === 0 ? (
              <p className="text-ui-xs text-muted-foreground">尚未选择文件</p>
            ) : (
              <>
                <p className="text-ui-xs font-medium text-foreground">
                  已选择 {files.length} 个文件，共 {formatBytes(totalSize)}
                </p>
                <ul className="mt-2 grid gap-1 text-ui-xs text-muted-foreground sm:grid-cols-2">
                  {files.map((file) => (
                    <li key={`${file.name}-${file.size}`} className="truncate" title={file.name}>
                      {file.name} · {formatBytes(file.size)}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        </div>

        {result && result.accepted > 0 && (
          <Alert variant="success">
            <AlertTitle>资料已加入索引队列</AlertTitle>
            <AlertDescription>
              已受理 {result.accepted} 个文件，可在下方“索引任务”中查看处理进度。
            </AlertDescription>
          </Alert>
        )}

        {result && result.skipped.length > 0 && (
          <Alert variant="destructive" role="alert">
            <AlertTitle>{result.accepted > 0 ? "部分文件未受理" : "资料上传失败"}</AlertTitle>
            <AlertDescription>
              <ul className="list-disc space-y-1 pl-5">
                {result.skipped.map((skipped, index) => (
                  <li key={`${skipped.filename}-${index}`}>
                    {skipped.filename}：{skipped.reason}
                  </li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}

        <div className="flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-ui-xs text-muted-foreground">
            选择文件并完成必填分类后才能上传；上传成功会自动刷新资料和任务列表。
          </p>
          <Button onClick={submit} disabled={submitting || !canSubmit} className="w-full sm:w-auto">
            {submitting ? "正在上传并刷新…" : `上传 ${files.length} 个文件`}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function DocumentsCard({
  documents,
  loading,
  onChange,
}: {
  documents: IndexedDocument[];
  loading: boolean;
  onChange: () => Promise<void> | void;
}) {
  const [filter, setFilter] = useState("");

  const visible = useMemo(() => {
    const query = filter.trim().toLowerCase();
    if (!query) return documents;
    return documents.filter(
      (document) =>
        document.doc_title.toLowerCase().includes(query) ||
        document.category.toLowerCase().includes(query),
    );
  }, [documents, filter]);

  async function onDelete(document: IndexedDocument) {
    const confirmed = confirm(
      `从索引中移除「${document.doc_title}」？\n` +
        `这将删除该资料的 ${document.parent_count} 个父段落及其所有子块。`,
    );
    if (!confirmed) return;
    const deleteFile = confirm("同时从磁盘删除源文件？（取消 = 仅清除索引，保留文件）");
    try {
      await api.adminDeleteIndexedDocument(document.source_path, deleteFile);
      await onChange();
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }

  return (
    <section className="space-y-3" aria-labelledby="indexed-documents-title">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h2 id="indexed-documents-title" className="text-ui-base font-semibold text-foreground">
            已索引资料
          </h2>
          <p className="mt-1 text-ui-xs text-muted-foreground">
            查看当前可检索资料，并按标题或分类快速筛选。
          </p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <label htmlFor="indexed-document-filter" className="sr-only">筛选已索引资料</label>
          <Input
            id="indexed-document-filter"
            type="search"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="按标题或分类筛选…"
            className="w-full sm:w-72"
          />
          <span className="whitespace-nowrap text-ui-xs text-muted-foreground" aria-live="polite">
            {filter ? `${visible.length} / ${documents.length}` : `共 ${documents.length} 个文档`}
          </span>
        </div>
      </div>

      {loading ? (
        <Card>
          <LoadingState className="min-h-40" label="正在加载已索引资料…" />
        </Card>
      ) : visible.length === 0 ? (
        <EmptyState
          title={filter ? "没有匹配的资料" : "暂无已索引资料"}
          description={filter ? `没有找到标题或分类包含“${filter}”的资料。` : "上传并完成索引后，资料会显示在这里。"}
        />
      ) : (
        <Card className="overflow-hidden shadow-surface">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[48rem] text-ui-sm">
              <caption className="sr-only">已索引资料的标题、分类、类型、父段落数量和操作</caption>
              <thead className="border-b border-border bg-surface-muted text-muted-foreground">
                <tr>
                  <th scope="col" className="px-4 py-3 text-left font-medium">标题</th>
                  <th scope="col" className="px-4 py-3 text-left font-medium">分类</th>
                  <th scope="col" className="px-4 py-3 text-left font-medium">类型</th>
                  <th scope="col" className="px-4 py-3 text-right font-medium">父段落</th>
                  <th scope="col" className="px-4 py-3 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {visible.map((document) => (
                  <tr
                    key={document.source_path}
                    className="bg-card transition-colors duration-normal hover:bg-surface-muted/60"
                  >
                    <td className="px-4 py-3">
                      <div className="max-w-md truncate font-medium text-foreground" title={document.doc_title}>
                        {document.doc_title}
                      </div>
                      <div className="mt-1 max-w-md truncate font-mono text-ui-xs text-muted-foreground" title={document.source_path}>
                        {document.source_path}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant="secondary">{document.category}</Badge>
                      {document.company && (
                        <p className="mt-1 text-ui-xs text-muted-foreground">{document.company}</p>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant="outline">{documentTypeLabel(document)}</Badge>
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                      {document.parent_count}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Button variant="destructive" size="sm" onClick={() => onDelete(document)}>
                        删除资料
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </section>
  );
}

function JobStatusCell({ job }: { job: IndexJob }) {
  const isActive = ACTIVE_STATUSES.has(job.status);
  const elapsed = useElapsed(isActive ? job.started_at ?? job.created_at : null);
  const meta = STATUS_META[job.status];

  return (
    <div>
      <Badge variant={meta?.variant ?? "secondary"} className="gap-1.5">
        {isActive && (
          <svg className="h-3 w-3 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z" />
          </svg>
        )}
        {meta?.label ?? job.status}
        {elapsed && <span className="opacity-70">{elapsed}</span>}
      </Badge>
      {isActive && meta?.hint && (
        <p className="mt-1 max-w-xs text-ui-xs text-muted-foreground">{meta.hint}</p>
      )}
      {job.error && (
        <p
          className="mt-1 max-w-sm whitespace-pre-wrap text-ui-xs leading-relaxed text-destructive"
          title={job.error}
        >
          {job.error.length > 200 ? `${job.error.slice(0, 200)}…` : job.error}
        </p>
      )}
    </div>
  );
}

function JobsCard({
  jobs,
  loading,
  onChange,
}: {
  jobs: IndexJob[];
  loading: boolean;
  onChange: () => Promise<void> | void;
}) {
  async function onRetry(job: IndexJob) {
    try {
      await api.adminRetryIndexJob(job.id);
      await onChange();
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }

  async function onDelete(job: IndexJob) {
    if (!confirm("删除该任务记录？（不影响已索引的内容）")) return;
    try {
      await api.adminDeleteIndexJob(job.id);
      await onChange();
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }

  const activeCount = jobs.filter((job) => ACTIVE_STATUSES.has(job.status)).length;

  return (
    <section className="space-y-3" aria-labelledby="index-jobs-title">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 id="index-jobs-title" className="text-ui-base font-semibold text-foreground">
            索引任务
          </h2>
          <p className="mt-1 text-ui-xs text-muted-foreground">
            活跃任务每 3 秒自动刷新；完成后会同步更新已索引资料。
          </p>
        </div>
        <span className="text-ui-xs text-muted-foreground" aria-live="polite">
          共 {jobs.length} 个任务{activeCount > 0 ? `，${activeCount} 个处理中` : ""}
        </span>
      </div>

      {loading ? (
        <Card>
          <LoadingState className="min-h-40" label="正在加载索引任务…" />
        </Card>
      ) : jobs.length === 0 ? (
        <EmptyState
          title="暂无索引任务"
          description="上传资料后，排队、解析、切块和嵌入状态会显示在这里。"
        />
      ) : (
        <Card className="overflow-hidden shadow-surface">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[70rem] text-ui-sm">
              <caption className="sr-only">索引任务的文件、分类、状态、上传者、时间、规模和操作</caption>
              <thead className="border-b border-border bg-surface-muted text-muted-foreground">
                <tr>
                  <th scope="col" className="px-4 py-3 text-left font-medium">文件</th>
                  <th scope="col" className="px-4 py-3 text-left font-medium">分类</th>
                  <th scope="col" className="px-4 py-3 text-left font-medium">状态</th>
                  <th scope="col" className="px-4 py-3 text-left font-medium">上传者</th>
                  <th scope="col" className="px-4 py-3 text-left font-medium">时间</th>
                  <th scope="col" className="px-4 py-3 text-right font-medium">规模</th>
                  <th scope="col" className="px-4 py-3 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {jobs.map((job) => (
                  <tr
                    key={job.id}
                    className="bg-card align-top transition-colors duration-normal hover:bg-surface-muted/60"
                  >
                    <td className="px-4 py-3">
                      <div className="max-w-xs truncate font-medium text-foreground" title={job.filename}>
                        {job.filename}
                      </div>
                      <div className="mt-1 text-ui-xs text-muted-foreground">{formatBytes(job.file_size)}</div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant="secondary">{job.category}</Badge>
                    </td>
                    <td className="px-4 py-3">
                      <JobStatusCell job={job} />
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      <div>{job.real_name || "—"}</div>
                      {job.employee_id && <div className="mt-1 text-ui-xs">{job.employee_id}</div>}
                    </td>
                    <td className="px-4 py-3 text-ui-xs text-muted-foreground">
                      <div>提交 {formatAdminDate(job.created_at)}</div>
                      {job.started_at && !job.finished_at && (
                        <div className="mt-1">开始 {formatAdminDate(job.started_at)}</div>
                      )}
                      {job.finished_at && <div className="mt-1">完成 {formatAdminDate(job.finished_at)}</div>}
                    </td>
                    <td className="px-4 py-3 text-right text-ui-xs tabular-nums text-muted-foreground">
                      {job.status === "done" && job.parents != null && job.children != null
                        ? `${job.parents} 父 / ${job.children} 子`
                        : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-2 whitespace-nowrap">
                        {(job.status === "failed" || job.status === "done") && (
                          <Button variant="outline" size="sm" onClick={() => onRetry(job)}>
                            重试
                          </Button>
                        )}
                        {(job.status === "done" || job.status === "failed") && (
                          <Button variant="ghost" size="sm" onClick={() => onDelete(job)} className="text-destructive hover:text-destructive">
                            删除记录
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </section>
  );
}
