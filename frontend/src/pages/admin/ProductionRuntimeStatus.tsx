import { Activity, Cpu, HardDrive, RefreshCw, Server, Thermometer } from "lucide-react";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { IconButton } from "../../components/ui/icon-button";
import { LoadingState } from "../../components/ui/loading-state";
import type { AppSystemMetrics, GpuSystemMetrics, SystemOverview } from "../../types";
import { formatAdminDate, formatBytes } from "./admin-formatters";

type ProductionRuntimeStatusProps = {
  data: SystemOverview | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
};

function statusLabel(status: AppSystemMetrics["status"] | GpuSystemMetrics["status"]) {
  if (status === "healthy") return "运行正常";
  if (status === "degraded") return "运行受限";
  return "暂不可用";
}

function statusVariant(status: AppSystemMetrics["status"] | GpuSystemMetrics["status"]) {
  if (status === "healthy") return "success" as const;
  if (status === "degraded") return "warning" as const;
  return "destructive" as const;
}

function usageTone(percent: number) {
  if (percent >= 85) return "bg-destructive";
  if (percent >= 70) return "bg-warning";
  return "bg-primary";
}

function UsageBar({ label, value, detail, percent }: { label: string; value: string; detail: string; percent: number | null }) {
  const safePercent = percent === null ? 0 : Math.max(0, Math.min(100, percent));
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-3 text-ui-xs">
        <span className="font-medium text-foreground">{label}</span>
        <span className="tabular-nums text-muted-foreground">{value}</span>
      </div>
      <div
        className="h-2 overflow-hidden rounded-full bg-surface-muted"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent === null ? undefined : Math.round(safePercent)}
      >
        <span className={`block h-full rounded-full transition-[width] duration-normal ${percent === null ? "bg-border" : usageTone(safePercent)}`} style={{ width: `${percent === null ? 100 : safePercent}%` }} />
      </div>
      <p className="text-ui-xs text-muted-foreground">{detail}</p>
    </div>
  );
}

function appCard(app: AppSystemMetrics) {
  const memoryPercent = app.memory_used_bytes !== null && app.memory_total_bytes ? (app.memory_used_bytes / app.memory_total_bytes) * 100 : null;
  const diskPercent = app.disk_used_bytes !== null && app.disk_total_bytes ? (app.disk_used_bytes / app.disk_total_bytes) * 100 : null;
  return (
    <div className="space-y-4 border border-border bg-card p-4 sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-ui-md bg-primary/10 text-primary"><Server className="size-4" /></span>
          <div className="min-w-0"><h3 className="text-ui-sm font-semibold text-foreground">应用服务器</h3><p className="mt-1 text-ui-xs text-muted-foreground">FastAPI 与管理后台</p></div>
        </div>
        <Badge variant={statusVariant(app.status)}>{statusLabel(app.status)}</Badge>
      </div>
      <div className="space-y-4">
        <UsageBar label="CPU 使用率" value={app.cpu_percent === null ? "—" : `${app.cpu_percent.toFixed(1)}%`} detail="应用节点当前计算占用" percent={app.cpu_percent} />
        <UsageBar label="内存" value={app.memory_used_bytes === null || app.memory_total_bytes === null ? "—" : `${formatBytes(app.memory_used_bytes)} / ${formatBytes(app.memory_total_bytes)}`} detail="应用节点可见内存" percent={memoryPercent} />
        <UsageBar label="业务数据盘" value={app.disk_used_bytes === null || app.disk_total_bytes === null ? "—" : `${formatBytes(app.disk_used_bytes)} / ${formatBytes(app.disk_total_bytes)}`} detail="数据库、缓存与运行数据所在存储" percent={diskPercent} />
      </div>
      <p className="text-ui-xs text-muted-foreground">检查时间：{formatAdminDate(app.checked_at)}</p>
    </div>
  );
}

function gpuCard(gpu: GpuSystemMetrics) {
  const vramPercent = gpu.vram_used_bytes !== null && gpu.vram_total_bytes ? (gpu.vram_used_bytes / gpu.vram_total_bytes) * 100 : null;
  return (
    <div className="space-y-4 border border-border bg-card p-4 sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-ui-md bg-info/10 text-info"><Cpu className="size-4" /></span>
          <div className="min-w-0"><h3 className="text-ui-sm font-semibold text-foreground">GPU 服务器</h3><p className="mt-1 truncate text-ui-xs text-muted-foreground" title={gpu.device_name || undefined}>{gpu.device_name || "GPU 信息暂不可用"}</p></div>
        </div>
        <Badge variant={statusVariant(gpu.status)}>{gpu.stale ? "数据已过期" : statusLabel(gpu.status)}</Badge>
      </div>
      <div className="space-y-4">
        <UsageBar label="显存" value={gpu.vram_used_bytes === null || gpu.vram_total_bytes === null ? "—" : `${formatBytes(gpu.vram_used_bytes)} / ${formatBytes(gpu.vram_total_bytes)}`} detail="GPU 当前显存占用" percent={vramPercent} />
        <UsageBar label="GPU 利用率" value={gpu.utilization_percent === null ? "—" : `${gpu.utilization_percent.toFixed(1)}%`} detail="最近一次采样的 GPU 计算利用率" percent={gpu.utilization_percent} />
        <div className="grid grid-cols-2 gap-3 border-t border-border pt-3">
          <div className="flex items-start gap-2"><Thermometer className="mt-0.5 size-4 shrink-0 text-muted-foreground" /><div><p className="text-ui-xs text-muted-foreground">温度</p><p className="mt-1 text-ui-sm font-semibold tabular-nums">{gpu.temperature_celsius === null ? "—" : `${gpu.temperature_celsius.toFixed(0)}°C`}</p></div></div>
          <div className="flex items-start gap-2"><Activity className="mt-0.5 size-4 shrink-0 text-muted-foreground" /><div><p className="text-ui-xs text-muted-foreground">当前任务</p><p className="mt-1 text-ui-sm font-semibold tabular-nums">{gpu.inflight_requests === null ? "—" : gpu.inflight_requests}</p></div></div>
        </div>
      </div>
      <p className="text-ui-xs text-muted-foreground">检查时间：{formatAdminDate(gpu.checked_at)}{gpu.data_age_seconds && gpu.data_age_seconds > 0 ? ` · ${gpu.data_age_seconds} 秒前数据` : ""}</p>
    </div>
  );
}

export function ProductionRuntimeStatus({ data, loading, error, onRefresh }: ProductionRuntimeStatusProps) {
  return (
    <section aria-labelledby="overview-runtime-heading" className="space-y-3">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 id="overview-runtime-heading" className="text-ui-base font-semibold text-foreground">生产运行状态</h2>
          <p className="mt-1 text-ui-xs text-muted-foreground">查看应用节点和 GPU 节点的当前资源占用。</p>
        </div>
        <IconButton label="刷新生产运行状态" onClick={onRefresh} disabled={loading}>
          <RefreshCw className={loading ? "size-4 animate-spin" : "size-4"} />
        </IconButton>
      </div>
      {loading && !data ? <div className="border border-border bg-card"><LoadingState className="min-h-40" label="正在检查生产运行状态…" /></div> : error && !data ? <div role="alert" className="flex flex-col gap-3 border border-destructive/30 bg-destructive/5 px-4 py-5 sm:flex-row sm:items-center sm:justify-between"><p className="text-ui-sm text-destructive">生产运行状态暂不可用：{error}</p><Button variant="outline" size="sm" onClick={onRefresh}>重试</Button></div> : data ? <>
        <div className="flex flex-col gap-2 border border-border bg-surface-muted/60 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2 text-ui-sm"><HardDrive className="size-4 text-muted-foreground" /><span className="font-medium text-foreground">部署关系</span><Badge variant={data.topology === "shared" ? "info" : data.topology === "separate" ? "secondary" : "warning"}>{data.topology === "shared" ? "同机部署" : data.topology === "separate" ? "分离部署" : "待确认"}</Badge></div>
          <span className="text-ui-xs text-muted-foreground">统一检查时间：{formatAdminDate(data.checked_at)}</span>
        </div>
        {error && <p role="status" className="text-ui-xs text-warning">GPU 节点连接不稳定，页面保留最近一次可信数据。</p>}
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">{appCard(data.app)}{gpuCard(data.gpu)}</div>
      </> : null}
    </section>
  );
}
