import { RefreshCw, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import { Select } from "../../components/ui/select";
import { cn } from "../../lib/utils";
import type { ManagedCategory, ManagedIndexJob, ManagedIndexJobList } from "../../types";
import { formatAdminDate } from "./admin-formatters";

const PAGE_SIZE = 25;
const ACTIVE_STATUSES = new Set([
  "pending",
  "uploading",
  "queued_mineru",
  "parsing",
  "chunking",
  "summarizing",
  "embedding",
]);

type StatusFilter = "all" | "processing" | "ready" | "failed";
type StatusVariant = "secondary" | "success" | "warning" | "destructive" | "info";

const EMPTY_LIST: ManagedIndexJobList = { jobs: [], total: 0, status_counts: {} };

const STATUS_META: Record<string, { label: string; hint: string; variant: StatusVariant }> = {
  pending: { label: "排队中", hint: "等待发布处理", variant: "secondary" },
  uploading: { label: "上传中", hint: "正在准备资料", variant: "info" },
  queued_mineru: { label: "等待解析", hint: "等待解析器资源", variant: "warning" },
  parsing: { label: "解析中", hint: "正在提取文档内容", variant: "info" },
  chunking: { label: "切分中", hint: "正在生成检索内容块", variant: "info" },
  summarizing: { label: "生成摘要", hint: "正在处理表格摘要", variant: "warning" },
  embedding: { label: "写入索引", hint: "正在写入向量索引", variant: "warning" },
  done: { label: "已发布", hint: "当前版本可检索", variant: "success" },
  failed: { label: "发布失败", hint: "请查看失败原因", variant: "destructive" },
};

function documentTypeLabel(docType: string | null): string {
  return {
    pdf: "PDF",
    markdown: "Markdown",
    docx: "Word",
    xlsx: "Excel",
    pptx: "PPT",
    transcript: "视频转写",
  }[docType || ""] || docType || "未知";
}

export function AdminDocumentsPage() {
  const [listing, setListing] = useState<ManagedIndexJobList>(EMPTY_LIST);
  const [categories, setCategories] = useState<ManagedCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [docType, setDocType] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [history, setHistory] = useState(false);
  const [page, setPage] = useState(0);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(searchInput.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    setPage(0);
  }, [query, categoryId, docType, status, history]);

  const params = useMemo(() => ({
    query: query || undefined,
    category_id: categoryId || undefined,
    doc_type: docType || undefined,
    status: status === "all" ? undefined : status,
    history,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  }), [query, categoryId, docType, status, history, page]);

  const load = useCallback(async (background = false) => {
    background ? setRefreshing(true) : setLoading(true);
    setError(null);
    try {
      setListing(await api.managedContentIndexJobs(params));
    } catch (caught: any) {
      setError(caught?.message || String(caught));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [params]);

  useEffect(() => {
    api.managedCategories(false)
      .then(setCategories)
      .catch((caught: any) => setError(caught?.message || String(caught)));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const hasActive = listing.jobs.some((job) => ACTIVE_STATUSES.has(job.status));
  useEffect(() => {
    if (!hasActive) return;
    const timer = window.setInterval(() => void load(true), 3000);
    return () => window.clearInterval(timer);
  }, [hasActive, load]);

  const counts = listing.status_counts;
  const allCount = Object.values(counts).reduce((sum, value) => sum + value, 0);
  const pageCount = Math.max(1, Math.ceil(listing.total / PAGE_SIZE));
  const hasFilters = Boolean(query || categoryId || docType || status !== "all");

  return (
    <section className="space-y-5" aria-labelledby="admin-documents-title">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-ui-xs font-medium uppercase tracking-[0.14em] text-primary">知识库维护</p>
          <h1 id="admin-documents-title" className="mt-1 text-ui-2xl font-semibold text-foreground">
            索引监控
          </h1>
          <p className="mt-1 max-w-3xl text-ui-sm text-muted-foreground">
            跟踪资料库的发布处理状态；资料整理、确认、发布和失败重试请在“资料库”完成。
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          disabled={loading || refreshing}
          onClick={() => void load(true)}
        >
          <RefreshCw className={cn("size-4", refreshing && "animate-spin")} />
          {refreshing ? "刷新中" : "刷新"}
        </Button>
      </header>

      {error && (
        <ErrorState
          title="发布任务加载失败"
          description={error}
          action={<Button variant="outline" size="sm" onClick={() => void load()}>重新加载</Button>}
        />
      )}

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="发布任务状态概览">
        <SummaryCard label="全部任务" value={allCount} />
        <SummaryCard label="处理中" value={counts.processing || 0} tone="warning" />
        <SummaryCard label="已发布" value={counts.ready || 0} tone="success" />
        <SummaryCard label="发布失败" value={counts.failed || 0} tone="destructive" />
      </section>

      <section className="space-y-3" aria-labelledby="managed-index-title">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <h2 id="managed-index-title" className="text-ui-base font-semibold text-foreground">资料库发布任务</h2>
            <p className="mt-1 text-ui-xs text-muted-foreground">
              {history ? "正在显示全部历史尝试。" : "每个资料版本仅显示最新一次发布尝试。"}
            </p>
          </div>
          <div className="grid gap-2 sm:grid-cols-2 xl:flex xl:items-center">
            <label className="relative block sm:col-span-2 xl:w-72">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <span className="sr-only">搜索发布任务</span>
              <Input
                type="search"
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                placeholder="搜索名称、文件名或分类…"
                className="pl-9"
              />
            </label>
            <Select aria-label="按数据库分类筛选" value={categoryId} onChange={(event) => setCategoryId(event.target.value)} className="xl:w-52">
              <option value="">全部分类</option>
              {categories.map((category) => (
                <option key={category.id} value={category.id}>{category.full_path}</option>
              ))}
            </Select>
            <Select aria-label="按文件类型筛选" value={docType} onChange={(event) => setDocType(event.target.value)} className="xl:w-36">
              <option value="">全部类型</option>
              <option value="pdf">PDF</option>
              <option value="markdown">Markdown</option>
              <option value="docx">Word</option>
              <option value="xlsx">Excel</option>
              <option value="pptx">PPT</option>
              <option value="transcript">视频转写</option>
            </Select>
          </div>
        </div>

        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap gap-2" aria-label="按发布状态筛选">
            {([
              ["all", "全部"],
              ["processing", "处理中"],
              ["ready", "已发布"],
              ["failed", "发布失败"],
            ] as const).map(([value, label]) => (
              <Button key={value} size="sm" variant={status === value ? "default" : "outline"} onClick={() => setStatus(value)}>
                {label}
              </Button>
            ))}
          </div>
          <Button size="sm" variant="outline" aria-pressed={history} onClick={() => setHistory((value) => !value)}>
            {history ? "仅看最新尝试" : "查看历史尝试"}
          </Button>
        </div>

        {loading ? (
          <Card><LoadingState className="min-h-64" label="正在加载发布任务…" /></Card>
        ) : listing.jobs.length === 0 ? (
          <EmptyState
            title={hasFilters ? "没有符合条件的发布任务" : "暂无发布任务"}
            description={hasFilters ? "请调整搜索或筛选条件。" : "资料在资料库中发布后，处理状态会显示在这里。"}
          />
        ) : (
          <ManagedJobsTable jobs={listing.jobs} history={history} />
        )}

        {!loading && listing.total > 0 && (
          <div className="flex flex-col gap-2 text-ui-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
            <span>共 {listing.total} 条任务，第 {page + 1} / {pageCount} 页</span>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage((value) => value - 1)}>上一页</Button>
              <Button size="sm" variant="outline" disabled={page + 1 >= pageCount} onClick={() => setPage((value) => value + 1)}>下一页</Button>
            </div>
          </div>
        )}
      </section>
    </section>
  );
}

function ManagedJobsTable({ jobs, history }: { jobs: ManagedIndexJob[]; history: boolean }) {
  return (
    <div className="overflow-hidden border border-border">
      <table className="block w-full text-ui-sm lg:table lg:min-w-[52rem]">
        <caption className="sr-only">资料库发布任务、数据库分类、文件类型、状态和更新时间</caption>
        <thead className="hidden border-b border-border bg-surface-muted text-left text-muted-foreground lg:table-header-group">
          <tr>
            <th className="px-4 py-3 font-medium">资料</th>
            <th className="px-4 py-3 font-medium">数据库分类</th>
            <th className="px-4 py-3 font-medium">类型</th>
            <th className="px-4 py-3 font-medium">状态</th>
            <th className="px-4 py-3 font-medium">更新时间</th>
          </tr>
        </thead>
        <tbody className="block divide-y divide-border lg:table-row-group">
          {jobs.map((job) => {
            const meta = STATUS_META[job.status] || { label: job.status, hint: "状态待确认", variant: "secondary" as const };
            return (
              <tr key={job.id} className="grid grid-cols-2 gap-x-3 gap-y-3 p-4 lg:table-row lg:p-0">
                <td className="col-span-2 block min-w-0 lg:table-cell lg:px-4 lg:py-3">
                  <p className="break-words font-medium text-foreground" title={job.title || job.original_filename || undefined}>
                    {job.title || job.original_filename || "未命名资料"}
                  </p>
                  <p className="mt-1 break-all text-ui-xs text-muted-foreground">{job.original_filename || "—"}</p>
                  {job.attempt_count > 1 && (
                    <p className="mt-1 text-ui-xs text-muted-foreground">
                      共尝试 {job.attempt_count} 次{history ? ` · 当前第 ${job.attempt_number} 次` : ""}
                    </p>
                  )}
                </td>
                <td className="block text-ui-xs text-muted-foreground lg:table-cell lg:px-4 lg:py-3 lg:text-ui-sm lg:text-foreground">
                  <span className="lg:hidden">分类： </span>{job.category_label || "—"}
                </td>
                <td className="block text-right text-ui-xs text-muted-foreground lg:table-cell lg:px-4 lg:py-3 lg:text-left lg:text-ui-sm lg:text-foreground">
                  <span className="lg:hidden">类型： </span>{documentTypeLabel(job.doc_type)}
                </td>
                <td className="col-span-2 block lg:table-cell lg:px-4 lg:py-3">
                  <Badge variant={meta.variant}>{meta.label}</Badge>
                  <p className="mt-1 text-ui-xs text-muted-foreground">{meta.hint}</p>
                  {job.failure && (
                    <div className="mt-2 max-w-md space-y-1 text-ui-xs">
                      <p className="break-words text-destructive">{job.failure.message}</p>
                      <p className="break-words text-muted-foreground">{job.failure.recommended_action}</p>
                      <p className="text-muted-foreground">{job.failure.retryable ? "可在资料库重新发布" : "请先处理文件或系统配置"}</p>
                    </div>
                  )}
                </td>
                <td className="col-span-2 block text-ui-xs text-muted-foreground lg:table-cell lg:px-4 lg:py-3">
                  <span className="lg:hidden">更新时间： </span>{formatAdminDate(job.updated_at)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SummaryCard({ label, value, tone = "secondary" }: {
  label: string;
  value: number;
  tone?: "secondary" | "warning" | "success" | "destructive";
}) {
  const dotClass = {
    secondary: "bg-muted-foreground",
    warning: "bg-warning",
    success: "bg-success",
    destructive: "bg-destructive",
  }[tone];
  return (
    <Card className="flex items-center justify-between px-4 py-3 shadow-surface">
      <div className="flex items-center gap-2 text-ui-sm text-muted-foreground">
        <span className={cn("size-2 rounded-full", dotClass)} />
        {label}
      </div>
      <strong className="text-ui-xl tabular-nums text-foreground">{value}</strong>
    </Card>
  );
}
