import { useCallback, useEffect, useMemo, useState } from "react";
import { Archive, ArrowDown, ArrowUp, Copy, GripVertical, Plus, RefreshCw, Rocket, Save, ShieldCheck } from "lucide-react";
import { ApiError } from "../../api/client";
import { adminAsrApi } from "../../api/admin/asr";
import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { IconButton } from "../../components/ui/icon-button";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import { Select } from "../../components/ui/select";
import { toast } from "../../components/ui/toast";
import { formatAdminDate } from "../../lib/admin-formatters";
import { createRequestId } from "../../lib/request-id";
import type { AsrManagedProfile, AsrSettings, TranscriptionBase, TranscriptionScheme, TranscriptionSchemeParameters } from "../../types";

type View = "schemes" | "bases" | "releases";
type SchemeDraft = Pick<TranscriptionScheme, "name" | "description" | "base_id" | "parameters">;

const presetLabels = { natural: "自然分段", balanced: "均衡分段", fine: "精细分段", custom: "自定义分段" } as const;
const requestStatusLabels = { requested: "待发布处理", completed: "已完成", rejected: "已退回", cancelled: "已取消" } as const;
const defaultParameters: TranscriptionSchemeParameters = {
  segmentation_preset: "natural", max_duration_ms: null, max_chars: 500, merge_gap_ms: 1000,
  terminology_profile: "bim-engineering-v1", prompt_asset: "asr_engineering_zh_v2",
  preprocessing_preset: "standard-audio-v1", vad_preset: "service-default-v1", decode_preset: "service-default-v1",
};

function draftFromScheme(scheme: TranscriptionScheme): SchemeDraft {
  return { name: scheme.name, description: scheme.description, base_id: scheme.base_id, parameters: { ...scheme.parameters } };
}

function durationLabel(value: number | null) {
  return value === null ? "模型自然边界" : `${value / 1000} 秒`;
}

function ProfileReleasePanel({ profile, busy, onRelease }: { profile: AsrManagedProfile; busy: boolean; onRelease: () => void }) {
  return <Card><CardHeader className="gap-3 p-4 sm:flex-row sm:items-start sm:justify-between sm:p-5"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><CardTitle className="text-ui-lg">{profile.display_name}</CardTitle><Badge variant="success"><ShieldCheck className="mr-1 size-3.5" />资格通过</Badge></div><p className="mt-2 text-ui-sm text-muted-foreground">{profile.description}</p></div><Button onClick={onRelease} disabled={!profile.release_eligible || busy}><Rocket className="size-4" />申请发布</Button></CardHeader><CardContent className="space-y-4"><dl className="grid gap-3 border-y border-border py-4 sm:grid-cols-2 lg:grid-cols-4"><div><dt className="text-ui-xs text-muted-foreground">最长时间</dt><dd className="mt-1 text-ui-sm font-medium">{durationLabel(profile.segmentation?.max_segment_duration_ms ?? null)}</dd></div><div><dt className="text-ui-xs text-muted-foreground">最长字符</dt><dd className="mt-1 text-ui-sm font-medium">{profile.segmentation?.max_segment_chars ?? 0} 字</dd></div><div><dt className="text-ui-xs text-muted-foreground">解码参数</dt><dd className="mt-1 text-ui-sm font-medium">Beam {profile.decode.beam_size} · 温度 {profile.decode.temperature}</dd></div><div><dt className="text-ui-xs text-muted-foreground">Prompt 资产</dt><dd className="mt-1 break-words text-ui-sm font-medium">{profile.decode.prompt_asset_id ?? "无"}</dd></div></dl><div className="flex flex-wrap gap-2">{profile.protected_terms.map((term) => <Badge key={term} variant="secondary">{term}</Badge>)}</div></CardContent></Card>;
}

export function AdminAsrSettingsPage() {
  const [view, setView] = useState<View>("schemes");
  const [settings, setSettings] = useState<AsrSettings | null>(null);
  const [bases, setBases] = useState<TranscriptionBase[]>([]);
  const [schemes, setSchemes] = useState<TranscriptionScheme[]>([]);
  const [serverSchemes, setServerSchemes] = useState<TranscriptionScheme[]>([]);
  const [selectedId, setSelectedId] = useState<string | "new" | null>(null);
  const [draft, setDraft] = useState<SchemeDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [orderDirty, setOrderDirty] = useState(false);
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [copyOpen, setCopyOpen] = useState(false);
  const [copyName, setCopyName] = useState("");
  const [releaseOpen, setReleaseOpen] = useState(false);
  const [releaseReason, setReleaseReason] = useState("");
  const [releaseKey, setReleaseKey] = useState(createRequestId);
  const [releaseProfileId, setReleaseProfileId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [nextSettings, nextBases, nextSchemes] = await Promise.all([adminAsrApi.get(), adminAsrApi.bases(), adminAsrApi.schemes(true)]);
      setSettings(nextSettings); setBases(nextBases); setSchemes(nextSchemes); setServerSchemes(nextSchemes); setOrderDirty(false);
      setSelectedId((current) => {
        const nextId = current && current !== "new" && nextSchemes.some((item) => item.id === current) ? current : nextSchemes[0]?.id ?? null;
        const selected = nextSchemes.find((item) => item.id === nextId);
        setDraft(selected ? draftFromScheme(selected) : null);
        return nextId;
      });
      setReleaseProfileId((current) => {
        const currentProfile = current ? nextSettings.profiles.find((item) => item.profile_id === current) : null;
        if (currentProfile) return currentProfile.profile_id;
        const eligible = nextSettings.profiles.find((item) => item.release_eligible && item.admission === "enabled" && item.availability === "available");
        return eligible?.profile_id ?? nextSettings.profiles[0]?.profile_id ?? null;
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "转录配置加载失败");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);
  const selected = schemes.find((item) => item.id === selectedId) ?? null;
  const releaseProfile = settings?.profiles.find((item) => item.profile_id === releaseProfileId) ?? null;
  const admittedBases = bases.filter((base) => base.admission === "enabled" && base.availability !== "disabled");

  function selectScheme(scheme: TranscriptionScheme) { setSelectedId(scheme.id); setDraft(draftFromScheme(scheme)); setNotice(null); }
  function beginCreate() {
    const base = admittedBases[0]; if (!base) return;
    setSelectedId("new"); setDraft({ name: "", description: "", base_id: base.id, parameters: { ...defaultParameters, ...base.defaults } as TranscriptionSchemeParameters }); setNotice(null);
  }
  function moveScheme(id: string, delta: number) {
    setSchemes((current) => { const index = current.findIndex((item) => item.id === id); const target = index + delta; if (index < 0 || target < 0 || target >= current.length) return current; const next = [...current]; [next[index], next[target]] = [next[target], next[index]]; return next; });
    setOrderDirty(true); setNotice("排序尚未保存");
  }
  function dropScheme(targetId: string) {
    if (!draggedId || draggedId === targetId) return;
    setSchemes((current) => { const from = current.findIndex((item) => item.id === draggedId); const to = current.findIndex((item) => item.id === targetId); const next = [...current]; const [moved] = next.splice(from, 1); next.splice(to, 0, moved); return next; });
    setDraggedId(null); setOrderDirty(true); setNotice("排序尚未保存");
  }

  async function saveOrder() {
    setBusy("order"); setError(null);
    try {
      const next = await adminAsrApi.reorderSchemes(schemes.map((item) => ({ id: item.id, expected_version: item.version })));
      setSchemes(next); setServerSchemes(next); setOrderDirty(false); setNotice("方案顺序已保存"); toast.success("方案顺序已保存");
    } catch (cause) {
      setSchemes(serverSchemes); setOrderDirty(false);
      setError(cause instanceof ApiError && cause.status === 409 ? "排序版本冲突，本地顺序已恢复；请刷新后重试。" : cause instanceof Error ? cause.message : "方案顺序保存失败");
    } finally { setBusy(null); }
  }

  async function saveScheme() {
    if (!draft || !draft.name.trim()) return;
    setBusy("save"); setError(null);
    try {
      const saved = selectedId === "new"
        ? await adminAsrApi.createScheme({ ...draft, name: draft.name.trim(), description: draft.description.trim() })
        : await adminAsrApi.updateScheme(selectedId!, { name: draft.name.trim(), description: draft.description.trim(), parameters: draft.parameters, expected_version: selected!.version });
      const next = selectedId === "new" ? [...schemes, saved] : schemes.map((item) => item.id === saved.id ? saved : item);
      setSchemes(next); setServerSchemes(next); setSelectedId(saved.id); setDraft(draftFromScheme(saved)); setNotice(selectedId === "new" ? "方案已创建" : "方案已保存"); toast.success(selectedId === "new" ? "方案已创建" : "方案已保存");
    } catch (cause) { setError(cause instanceof ApiError && cause.status === 409 ? "方案已被其他管理员修改，请刷新后重试。" : cause instanceof Error ? cause.message : "方案保存失败"); }
    finally { setBusy(null); }
  }

  async function patchState(change: { enabled?: boolean; archived?: boolean }) {
    if (!selected) return; setBusy("state"); setError(null);
    try {
      const saved = await adminAsrApi.updateScheme(selected.id, { ...change, expected_version: selected.version });
      const next = schemes.map((item) => item.id === saved.id ? saved : item); setSchemes(next); setServerSchemes(next); setDraft(draftFromScheme(saved)); setNotice(change.archived ? "方案已归档" : saved.enabled ? "方案已启用" : "方案已停用");
    } catch (cause) { setError(cause instanceof ApiError && cause.status === 409 ? "方案状态已变化，请刷新后重试。" : cause instanceof Error ? cause.message : "方案状态更新失败"); }
    finally { setBusy(null); }
  }

  async function copyScheme() {
    if (!selected || !copyName.trim()) return; setBusy("copy");
    try {
      const copied = await adminAsrApi.copyScheme(selected.id, { name: copyName.trim() });
      const next = [...schemes, copied]; setSchemes(next); setServerSchemes(next); setSelectedId(copied.id); setDraft(draftFromScheme(copied)); setCopyOpen(false); setCopyName(""); setNotice("副本已创建"); toast.success("方案副本已创建");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "复制方案失败"); }
    finally { setBusy(null); }
  }

  async function requestRelease() {
    if (!releaseProfile) return; setBusy("release");
    try {
      const request = await adminAsrApi.requestRelease({ profile_id: releaseProfile.profile_id, request_idempotency_key: releaseKey, request_reason: releaseReason.trim() || null });
      setSettings((current) => current ? { ...current, release_requests: [request, ...current.release_requests.filter((item) => item.request_id !== request.request_id)] } : current);
      setReleaseOpen(false); setReleaseReason(""); setReleaseKey(createRequestId()); toast.success("发布申请已记录");
    } catch (cause) { toast.error(cause instanceof Error ? cause.message : "发布申请失败"); }
    finally { setBusy(null); }
  }

  if (loading && !settings) return <LoadingState className="min-h-64" label="正在读取转录配置…" />;
  if (error && !settings) return <ErrorState description={error} action={<Button variant="outline" onClick={() => void load()}>重新加载</Button>} />;
  if (!settings) return null;
  const serviceLabel = settings.service.status === "healthy" ? "服务正常" : settings.service.status === "disabled" ? "服务未启用" : settings.service.status === "degraded" ? "服务受限" : "服务不可用";

  return <section aria-labelledby="admin-asr-title" className="space-y-4 sm:space-y-5">
    <header className="flex items-start justify-between gap-3"><div><p className="text-ui-xs font-medium text-muted-foreground">总览</p><h1 id="admin-asr-title" className="mt-1 text-ui-2xl font-semibold">转录配置</h1><div className="mt-2"><Badge variant={settings.service.status === "healthy" ? "success" : "warning"}>{serviceLabel}</Badge></div></div><IconButton label="刷新转录配置" onClick={() => void load()} disabled={loading}><RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} /></IconButton></header>
    {error && <Alert variant="destructive" role="alert"><AlertTitle>操作失败</AlertTitle><AlertDescription>{error}<Button className="ml-2" size="sm" variant="outline" onClick={() => void load()}>刷新</Button></AlertDescription></Alert>}
    {notice && <Alert role="status"><AlertTitle>操作完成</AlertTitle><AlertDescription>{notice}</AlertDescription></Alert>}
    <nav className="flex overflow-x-auto border-b border-border" aria-label="转录配置视图">{([['schemes','转录方案'],['bases','底座与参数'],['releases','发布记录']] as [View,string][]).map(([id,label]) => <button key={id} type="button" aria-current={view === id ? "page" : undefined} className={`min-h-11 whitespace-nowrap border-b-2 px-4 text-ui-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${view === id ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`} onClick={() => setView(id)}>{label}</button>)}</nav>

    {view === "schemes" && <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(18rem,0.85fr)_minmax(0,1.5fr)]">
      <section aria-labelledby="scheme-list-title" className="min-w-0 border-y border-border lg:border-r lg:border-t-0 lg:pr-4"><div className="flex items-center justify-between gap-3 py-3"><div><h2 id="scheme-list-title" className="text-ui-base font-semibold">方案顺序</h2><p className="text-ui-xs text-muted-foreground">{schemes.filter((item) => item.enabled && !item.archived).length} 个可选方案</p></div><Button size="sm" onClick={beginCreate} disabled={admittedBases.length === 0 || busy !== null}><Plus className="size-4" />新建</Button></div>
        {schemes.length === 0 ? <EmptyState title="暂无转录方案" /> : <ol className="divide-y divide-border border-y border-border" aria-label="转录方案排序列表">{schemes.map((scheme, index) => <li key={scheme.id} draggable={busy === null} onDragStart={() => setDraggedId(scheme.id)} onDragOver={(event) => event.preventDefault()} onDrop={() => dropScheme(scheme.id)} className={scheme.archived ? "opacity-60" : ""}><div className={`flex min-h-16 items-center gap-2 border-l-2 px-2 py-2 ${selectedId === scheme.id ? "border-l-primary bg-primary/5" : "border-l-transparent"}`}><GripVertical className="size-4 shrink-0 cursor-grab text-muted-foreground" aria-hidden="true" /><button type="button" className="min-w-0 flex-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => selectScheme(scheme)}><span className="block break-words text-ui-sm font-medium">{scheme.name}</span><span className="mt-1 flex flex-wrap gap-1"><Badge variant={scheme.enabled && !scheme.archived ? "success" : "secondary"}>{scheme.archived ? "已归档" : scheme.enabled ? "已启用" : "已停用"}</Badge>{scheme.system_preset && <Badge variant="outline">系统预置</Badge>}</span></button><div className="flex shrink-0"><IconButton label={`上移 ${scheme.name}`} title="上移" disabled={index === 0 || busy !== null} onClick={() => moveScheme(scheme.id, -1)}><ArrowUp className="size-4" /></IconButton><IconButton label={`下移 ${scheme.name}`} title="下移" disabled={index === schemes.length - 1 || busy !== null} onClick={() => moveScheme(scheme.id, 1)}><ArrowDown className="size-4" /></IconButton></div></div></li>)}</ol>}
        <div className="flex flex-wrap justify-end gap-2 py-3"><Button size="sm" variant="outline" disabled={!orderDirty || busy !== null} onClick={() => { setSchemes(serverSchemes); setOrderDirty(false); setNotice(null); }}>恢复</Button><Button size="sm" disabled={!orderDirty || busy !== null} onClick={() => void saveOrder()}><Save className="size-4" />{busy === "order" ? "保存中…" : "保存顺序"}</Button></div>
      </section>
      <section aria-labelledby="scheme-editor-title" className="min-w-0">{!draft ? <EmptyState title="选择一个转录方案" /> : <div className="space-y-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 id="scheme-editor-title" className="text-ui-lg font-semibold">{selectedId === "new" ? "新建转录方案" : selected?.name}</h2>{selected && <p className="mt-1 text-ui-xs text-muted-foreground">版本 {selected.version} · 更新于 {formatAdminDate(selected.updated_at)}</p>}</div>{selected && <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={busy !== null} onClick={() => { setCopyName(`${selected.name} 副本`); setCopyOpen(true); }}><Copy className="size-4" />复制</Button><Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void patchState({ enabled: !selected.enabled })}>{selected.enabled ? "停用" : "启用"}</Button>{!selected.system_preset && !selected.archived && <Button size="sm" variant="destructive" disabled={busy !== null} onClick={() => void patchState({ archived: true })}><Archive className="size-4" />归档</Button>}</div>}</div>
        <fieldset disabled={busy !== null || Boolean(selected?.system_preset)} className="grid gap-4 sm:grid-cols-2"><label className="space-y-1.5 text-ui-sm font-medium"><span>方案名称</span><Input value={draft.name} maxLength={120} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label><label className="space-y-1.5 text-ui-sm font-medium"><span>转录底座</span><Select value={draft.base_id} disabled={selectedId !== "new" || busy !== null} onChange={(e) => setDraft({ ...draft, base_id: e.target.value })}>{admittedBases.map((base) => <option key={base.id} value={base.id}>{base.model}</option>)}</Select></label><label className="space-y-1.5 text-ui-sm font-medium sm:col-span-2"><span>说明</span><textarea className="min-h-20 w-full resize-y rounded-ui-md border border-input bg-background px-3 py-2 text-ui-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" value={draft.description} maxLength={500} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></label>
          <label className="space-y-1.5 text-ui-sm font-medium"><span>分段模式</span><Select value={draft.parameters.segmentation_preset} onChange={(e) => setDraft({ ...draft, parameters: { ...draft.parameters, segmentation_preset: e.target.value as TranscriptionSchemeParameters['segmentation_preset'] } })}>{Object.entries(presetLabels).map(([value,label]) => <option key={value} value={value}>{label}</option>)}</Select></label><label className="space-y-1.5 text-ui-sm font-medium"><span>最长时长（毫秒）</span><Input type="number" min={1000} max={120000} value={draft.parameters.max_duration_ms ?? ""} placeholder="使用模型自然边界" onChange={(e) => setDraft({ ...draft, parameters: { ...draft.parameters, max_duration_ms: e.target.value ? Number(e.target.value) : null } })} /></label><label className="space-y-1.5 text-ui-sm font-medium"><span>最长字符</span><Input type="number" min={40} max={2000} value={draft.parameters.max_chars} onChange={(e) => setDraft({ ...draft, parameters: { ...draft.parameters, max_chars: Number(e.target.value) } })} /></label><label className="space-y-1.5 text-ui-sm font-medium"><span>合并间隔（毫秒）</span><Input type="number" min={0} max={5000} value={draft.parameters.merge_gap_ms} onChange={(e) => setDraft({ ...draft, parameters: { ...draft.parameters, merge_gap_ms: Number(e.target.value) } })} /></label><label className="space-y-1.5 text-ui-sm font-medium"><span>术语配置</span><Select value={draft.parameters.terminology_profile} onChange={(e) => setDraft({ ...draft, parameters: { ...draft.parameters, terminology_profile: e.target.value as TranscriptionSchemeParameters['terminology_profile'] } })}><option value="bim-engineering-v1">BIM 工程术语 v1</option><option value="none">不使用术语修正</option></Select></label><label className="space-y-1.5 text-ui-sm font-medium"><span>Prompt 资产</span><Select value={draft.parameters.prompt_asset} onChange={(e) => setDraft({ ...draft, parameters: { ...draft.parameters, prompt_asset: e.target.value as TranscriptionSchemeParameters['prompt_asset'] } })}><option value="asr_engineering_zh_v2">工程中文 v2</option><option value="asr_engineering_zh_v1">工程中文 v1</option></Select></label>
        </fieldset>{selected?.system_preset && <p className="text-ui-xs text-muted-foreground">系统预置的核心参数只读，可复制后编辑。</p>}<div className="flex justify-end"><Button disabled={busy !== null || Boolean(selected?.system_preset) || !draft.name.trim()} onClick={() => void saveScheme()}><Save className="size-4" />{busy === "save" ? "保存中…" : selectedId === "new" ? "创建方案" : "保存修改"}</Button></div></div>}</section>
    </div>}

    {view === "bases" && <section className="space-y-3" aria-labelledby="base-title"><div><h2 id="base-title" className="text-ui-lg font-semibold">底座与允许参数</h2></div>{bases.length === 0 ? <EmptyState title="暂无转录底座" /> : <div className="divide-y divide-border border-y border-border">{bases.map((base) => <article key={base.id} className="grid gap-3 py-4 md:grid-cols-[minmax(0,1fr)_minmax(18rem,1fr)]"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="text-ui-base font-semibold">{base.model}</h3><Badge variant={base.admission === "enabled" ? "success" : "secondary"}>{base.admission === "enabled" ? "允许使用" : "禁止新建方案"}</Badge><Badge variant="outline">{base.qualification}</Badge></div><p className="mt-1 break-words text-ui-sm text-muted-foreground">{base.provider} · revision {base.revision}</p></div><div><p className="text-ui-xs font-medium text-muted-foreground">受控能力</p><div className="mt-2 flex flex-wrap gap-2">{Object.entries(base.capabilities).map(([key,value]) => <Badge key={key} variant="secondary">{key}: {String(value)}</Badge>)}</div></div></article>)}</div>}</section>}

    {view === "releases" && <section className="space-y-5"><div className="grid gap-3 sm:grid-cols-[minmax(0,20rem)_1fr] sm:items-end"><label className="space-y-1.5 text-ui-sm font-medium"><span>发布 Profile</span><Select value={releaseProfileId ?? ""} onChange={(e) => setReleaseProfileId(e.target.value)}>{settings.profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.display_name}</option>)}</Select></label></div>{releaseProfile ? <ProfileReleasePanel profile={releaseProfile} busy={busy !== null} onRelease={() => setReleaseOpen(true)} /> : <EmptyState title="暂无已资格发布 Profile" />}<section aria-labelledby="release-list-title"><h2 id="release-list-title" className="text-ui-lg font-semibold">发布申请</h2>{settings.release_requests.length === 0 ? <EmptyState title="暂无发布申请" /> : <div className="mt-2 divide-y divide-border border-y border-border">{settings.release_requests.map((request) => <article key={request.request_id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between"><div><p className="text-ui-sm font-medium">{request.profile_display_name}</p><p className="mt-1 text-ui-xs text-muted-foreground">{request.requested_by_name ?? "已离职用户"} · {formatAdminDate(request.created_at)}{request.request_reason ? ` · ${request.request_reason}` : ""}</p></div><Badge variant={request.status === "completed" ? "success" : request.status === "rejected" ? "destructive" : "warning"}>{requestStatusLabels[request.status]}</Badge></article>)}</div>}</section><section aria-labelledby="audit-title"><h2 id="audit-title" className="text-ui-lg font-semibold">审计记录</h2>{settings.audit_events.length === 0 ? <p className="mt-2 text-ui-sm text-muted-foreground">暂无审计记录。</p> : <div className="mt-2 space-y-2">{settings.audit_events.map((event) => <p key={event.event_id} className="text-ui-sm text-muted-foreground">{formatAdminDate(event.created_at)} · {event.actor_name ?? "已离职用户"} · 申请发布 {event.profile_display_name}</p>)}</div>}</section></section>}

    <Dialog open={copyOpen} onOpenChange={(open) => { if (busy === null) setCopyOpen(open); }}><DialogContent><DialogHeader><DialogTitle>复制转录方案</DialogTitle><DialogDescription>{selected?.name}</DialogDescription></DialogHeader><label className="space-y-1.5 text-ui-sm font-medium"><span>副本名称</span><Input value={copyName} maxLength={120} onChange={(e) => setCopyName(e.target.value)} /></label><DialogFooter><Button variant="outline" disabled={busy !== null} onClick={() => setCopyOpen(false)}>取消</Button><Button disabled={busy !== null || !copyName.trim()} onClick={() => void copyScheme()}>{busy === "copy" ? "复制中…" : "创建副本"}</Button></DialogFooter></DialogContent></Dialog>
    <Dialog open={releaseOpen} onOpenChange={(open) => { if (busy === null) setReleaseOpen(open); }}><DialogContent><DialogHeader><DialogTitle>申请发布转录配置</DialogTitle><DialogDescription>{releaseProfile?.display_name}</DialogDescription></DialogHeader><label className="space-y-1.5 text-ui-sm font-medium"><span>申请原因（选填）</span><Input value={releaseReason} maxLength={500} onChange={(e) => setReleaseReason(e.target.value)} placeholder="例如：培训视频需要更密集的时间定位" /></label><DialogFooter><Button variant="outline" disabled={busy !== null} onClick={() => setReleaseOpen(false)}>取消</Button><Button disabled={busy !== null} onClick={() => void requestRelease()}>{busy === "release" ? "提交中…" : "确认申请"}</Button></DialogFooter></DialogContent></Dialog>
  </section>;
}
