import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, RefreshCw, Rocket, ShieldCheck } from "lucide-react";
import { adminAsrApi } from "../../api/admin/asr";
import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { IconButton } from "../../components/ui/icon-button";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import { toast } from "../../components/ui/toast";
import { formatAdminDate } from "../../lib/admin-formatters";
import { createRequestId } from "../../lib/request-id";
import type { AsrManagedProfile, AsrSettings } from "../../types";

const presetLabels = { natural: "自然", balanced: "均衡", fine: "细分" } as const;
const requestStatusLabels = {
  requested: "待发布处理",
  completed: "已完成",
  rejected: "已退回",
  cancelled: "已取消",
} as const;

function durationLabel(value: number | null) {
  return value === null ? "模型自然边界" : `${value / 1000} 秒`;
}

function ProfileChoice({
  profile,
  selected,
  onSelect,
}: {
  profile: AsrManagedProfile;
  selected: boolean;
  onSelect: () => void;
}) {
  const segmentation = profile.segmentation;
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={`min-w-0 rounded-ui-xl border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:p-4 ${selected ? "border-primary bg-primary/5" : "border-border bg-card hover:bg-surface-muted"}`}
    >
      <span className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-ui-base font-semibold text-foreground">
          {segmentation ? presetLabels[segmentation.preset] : profile.display_name}
        </span>
        {selected && <CheckCircle2 className="size-4 shrink-0 text-primary" aria-hidden="true" />}
      </span>
      <span className="mt-2 block text-ui-sm text-muted-foreground">最长 {durationLabel(segmentation?.max_segment_duration_ms ?? null)}</span>
      <span className="mt-1 block text-ui-xs text-muted-foreground">合并间隔 {segmentation?.max_merge_gap_ms ?? 0} ms</span>
    </button>
  );
}

export function AdminAsrSettingsPage() {
  const [data, setData] = useState<AsrSettings | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [releaseOpen, setReleaseOpen] = useState(false);
  const [releaseReason, setReleaseReason] = useState("");
  const [releaseKey, setReleaseKey] = useState(createRequestId);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await adminAsrApi.get();
      setData(next);
      setSelectedId((current) => current && next.profiles.some((item) => item.profile_id === current)
        ? current
        : next.profiles.find((item) => item.segmentation?.preset === "balanced")?.profile_id ?? next.profiles[0]?.profile_id ?? null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "转录配置加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const selected = useMemo(
    () => data?.profiles.find((item) => item.profile_id === selectedId) ?? null,
    [data, selectedId],
  );

  async function requestRelease() {
    if (!selected) return;
    setBusy(true);
    try {
      const request = await adminAsrApi.requestRelease({
        profile_id: selected.profile_id,
        request_idempotency_key: releaseKey,
        request_reason: releaseReason.trim() || null,
      });
      setData((current) => current ? {
        ...current,
        release_requests: [request, ...current.release_requests.filter((item) => item.request_id !== request.request_id)],
      } : current);
      setReleaseOpen(false);
      setReleaseReason("");
      setReleaseKey(createRequestId());
      toast.success("发布申请已记录");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "发布申请失败");
    } finally {
      setBusy(false);
    }
  }

  if (loading && !data) return <LoadingState className="min-h-64" label="正在读取转录配置…" />;
  if (error && !data) return <ErrorState description={error} action={<Button variant="outline" onClick={() => void load()}>重新加载</Button>} />;
  if (!data || data.profiles.length === 0) return <EmptyState title="暂无已资格转录配置" description="当前没有可比较或申请发布的版本。" />;

  const serviceLabel = data.service.status === "healthy" ? "服务正常" : data.service.status === "disabled" ? "服务未启用" : data.service.status === "degraded" ? "服务受限" : "服务不可用";
  const serviceVariant = data.service.status === "healthy" ? "success" : data.service.status === "disabled" ? "secondary" : "warning";

  return (
    <section aria-labelledby="admin-asr-title" className="space-y-4 sm:space-y-6">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="text-ui-xs font-medium text-muted-foreground">总览</p>
          <h1 id="admin-asr-title" className="mt-1 text-ui-2xl font-semibold text-foreground">转录配置</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge variant={serviceVariant}>{serviceLabel}</Badge>
            {data.service.queue_depth !== null && data.service.queue_limit !== null && (
              <span className="text-ui-xs text-muted-foreground">队列 {data.service.queue_depth}/{data.service.queue_limit}</span>
            )}
          </div>
        </div>
        <IconButton label="刷新转录配置" onClick={() => void load()} disabled={loading}>
          <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
        </IconButton>
      </header>

      {error && <Alert variant="destructive"><AlertTitle>刷新失败</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
      {data.service.status !== "healthy" && (
        <Alert variant="warning"><AlertTitle>{serviceLabel}</AlertTitle><AlertDescription>发布申请已暂停，现有转录记录不受影响。</AlertDescription></Alert>
      )}

      <section aria-labelledby="asr-preset-title" className="space-y-2 sm:space-y-3">
        <div>
          <h2 id="asr-preset-title" className="text-ui-lg font-semibold text-foreground">时间分段预设</h2>
          <p className="mt-1 text-ui-sm text-muted-foreground">WhisperX 对齐时间为基准，超长段优先在标点和词边界拆分。</p>
        </div>
        <div className="grid gap-2 sm:gap-3 md:grid-cols-3" role="group" aria-label="时间分段预设">
          {data.profiles.map((profile) => (
            <ProfileChoice key={profile.profile_id} profile={profile} selected={profile.profile_id === selectedId} onSelect={() => setSelectedId(profile.profile_id)} />
          ))}
        </div>
      </section>

      {selected && (
        <Card>
          <CardHeader className="gap-3 p-4 sm:flex-row sm:items-start sm:justify-between sm:space-y-0 sm:p-6">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <CardTitle className="text-ui-lg">{selected.display_name}</CardTitle>
                <Badge variant="success"><ShieldCheck className="mr-1 size-3.5" />资格通过</Badge>
                <Badge variant="outline">v{selected.profile_version}</Badge>
              </div>
              <p className="mt-2 text-ui-sm text-muted-foreground">{selected.description}</p>
            </div>
            <Button onClick={() => setReleaseOpen(true)} disabled={!selected.release_eligible || busy}>
              <Rocket className="size-4" />申请发布
            </Button>
          </CardHeader>
          <CardContent className="space-y-5">
            <dl className="grid gap-4 border-y border-border py-4 sm:grid-cols-2 lg:grid-cols-5">
              <div><dt className="text-ui-xs text-muted-foreground">最长时间</dt><dd className="mt-1 text-ui-sm font-medium">{durationLabel(selected.segmentation?.max_segment_duration_ms ?? null)}</dd></div>
              <div><dt className="text-ui-xs text-muted-foreground">最长字符</dt><dd className="mt-1 text-ui-sm font-medium">{selected.segmentation?.max_segment_chars ?? 0} 字</dd></div>
              <div><dt className="text-ui-xs text-muted-foreground">短段合并间隔</dt><dd className="mt-1 text-ui-sm font-medium">{selected.segmentation?.max_merge_gap_ms ?? 0} ms</dd></div>
              <div><dt className="text-ui-xs text-muted-foreground">解码参数</dt><dd className="mt-1 text-ui-sm font-medium">Beam {selected.decode.beam_size} · 温度 {selected.decode.temperature}</dd></div>
              <div><dt className="text-ui-xs text-muted-foreground">工程词数量</dt><dd className="mt-1 text-ui-sm font-medium">{selected.decode.hotword_count}</dd></div>
            </dl>

            <div>
              <h3 className="text-ui-sm font-semibold">时间戳切分规则</h3>
              <p className="mt-2 text-ui-sm text-muted-foreground">
                保留 WhisperX 原始对齐段；超过所选时长或字符上限时，依次在换行、句末标点、逗号和空格处切分。没有可用边界时才按字符切分，切点时间按原对齐段内的字符比例计算。
              </p>
            </div>

            <div>
              <h3 className="text-ui-sm font-semibold">固定工程词</h3>
              <div className="mt-2 flex flex-wrap gap-2">
                {selected.protected_terms.map((term) => <Badge key={term} variant="secondary">{term}</Badge>)}
              </div>
            </div>

            <details className="rounded-ui-lg border border-border px-4 py-3 text-ui-xs text-muted-foreground">
              <summary className="cursor-pointer font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">版本身份</summary>
              <dl className="mt-3 grid gap-2 break-all sm:grid-cols-[10rem_1fr]">
                <dt>应用配置哈希</dt><dd>{selected.application_config_hash}</dd>
                <dt>服务配置哈希</dt><dd>{selected.decode.service_profile_config_hash ?? "未取得"}</dd>
                <dt>Prompt 资产</dt><dd>{selected.decode.prompt_asset_id ?? "无"}</dd>
              </dl>
            </details>
          </CardContent>
        </Card>
      )}

      <section aria-labelledby="release-requests-title" className="space-y-3">
        <h2 id="release-requests-title" className="text-ui-lg font-semibold">发布申请</h2>
        {data.release_requests.length === 0 ? (
          <EmptyState title="暂无发布申请" />
        ) : (
          <div className="divide-y divide-border border-y border-border">
            {data.release_requests.map((request) => (
              <article key={request.request_id} className="flex flex-col gap-2 py-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="text-ui-sm font-medium text-foreground">{request.profile_display_name}</p>
                  <p className="mt-1 text-ui-xs text-muted-foreground">{request.requested_by_name ?? "已离职用户"} · {formatAdminDate(request.created_at)}{request.request_reason ? ` · ${request.request_reason}` : ""}</p>
                </div>
                <Badge variant={request.status === "completed" ? "success" : request.status === "rejected" ? "destructive" : "warning"}>{requestStatusLabels[request.status]}</Badge>
              </article>
            ))}
          </div>
        )}
      </section>

      <section aria-labelledby="asr-audit-title" className="space-y-3">
        <h2 id="asr-audit-title" className="text-ui-lg font-semibold">审计记录</h2>
        {data.audit_events.length === 0 ? <p className="text-ui-sm text-muted-foreground">暂无审计记录。</p> : (
          <div className="space-y-2">
            {data.audit_events.map((event) => (
              <p key={event.event_id} className="text-ui-sm text-muted-foreground">{formatAdminDate(event.created_at)} · {event.actor_name ?? "已离职用户"} · 申请发布 {event.profile_display_name}</p>
            ))}
          </div>
        )}
      </section>

      <Dialog open={releaseOpen} onOpenChange={(open) => { if (!busy) setReleaseOpen(open); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>申请发布转录配置</DialogTitle>
            <DialogDescription>{selected?.display_name}</DialogDescription>
          </DialogHeader>
          <label className="space-y-1.5 text-ui-sm font-medium">
            <span>申请原因（选填）</span>
            <Input value={releaseReason} maxLength={500} onChange={(event) => setReleaseReason(event.target.value)} placeholder="例如：培训视频需要更密集的时间定位" />
          </label>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setReleaseOpen(false)} disabled={busy}>取消</Button>
            <Button onClick={() => void requestRelease()} disabled={busy}>{busy ? "提交中…" : "确认申请"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
