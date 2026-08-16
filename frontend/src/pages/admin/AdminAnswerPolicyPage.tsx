import { useEffect, useState } from "react";
import { RotateCcw, Save } from "lucide-react";
import { adminAnswerPolicyApi } from "../../api/admin/answerPolicy";
import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { ErrorState } from "../../components/ui/error-state";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import type { AnswerPolicy, AnswerPolicyAuditEntry } from "../../types";
import { formatAdminDate } from "../../lib/admin-formatters";

type EditablePolicy = Pick<AnswerPolicy, "answer_temperature" | "answer_max_output_tokens" | "answer_context_chars" | "relevance_gate_enabled" | "relevance_min_score" | "relevance_min_rrf" | "relevance_min_margin">;

const defaults: EditablePolicy = {
  answer_temperature: 0.2,
  answer_max_output_tokens: 1200,
  answer_context_chars: 6000,
  relevance_gate_enabled: false,
  relevance_min_score: 0,
  relevance_min_rrf: 0,
  relevance_min_margin: 0,
};

function editable(policy: AnswerPolicy): EditablePolicy {
  return {
    answer_temperature: policy.answer_temperature,
    answer_max_output_tokens: policy.answer_max_output_tokens,
    answer_context_chars: policy.answer_context_chars,
    relevance_gate_enabled: policy.relevance_gate_enabled,
    relevance_min_score: policy.relevance_min_score,
    relevance_min_rrf: policy.relevance_min_rrf,
    relevance_min_margin: policy.relevance_min_margin,
  };
}

function changedVersion(entry: AnswerPolicyAuditEntry, key: keyof EditablePolicy) {
  try {
    const before = JSON.parse(entry.old_policy_json) as Partial<EditablePolicy>;
    const after = JSON.parse(entry.new_policy_json) as Partial<EditablePolicy>;
    return `${String(before[key] ?? "-")} -> ${String(after[key] ?? "-")}`;
  } catch {
    return "已变更";
  }
}

export function AdminAnswerPolicyPage() {
  const [policy, setPolicy] = useState<AnswerPolicy | null>(null);
  const [draft, setDraft] = useState<EditablePolicy>(defaults);
  const [audit, setAudit] = useState<AnswerPolicyAuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"save" | "reset" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [nextPolicy, nextAudit] = await Promise.all([adminAnswerPolicyApi.get(), adminAnswerPolicyApi.audit()]);
      setPolicy(nextPolicy);
      setDraft(editable(nextPolicy));
      setAudit(nextAudit.entries);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  const dirty = policy !== null && JSON.stringify(draft) !== JSON.stringify(editable(policy));
  const gateTurningOn = Boolean(draft.relevance_gate_enabled && !policy?.relevance_gate_enabled);

  async function save() {
    if (!dirty || busy) return;
    if (gateTurningOn && !reason.trim()) {
      setConfirmOpen(true);
      return;
    }
    setBusy("save");
    setNotice(null);
    try {
      const next = await adminAnswerPolicyApi.update({ ...draft, change_reason: reason.trim() || undefined });
      setPolicy(next);
      setDraft(editable(next));
      setReason("");
      setConfirmOpen(false);
      setNotice("回答策略已保存，新请求开始生效。已有流式回答继续使用原策略。");
      setAudit((await adminAnswerPolicyApi.audit()).entries);
    } catch (e: any) {
      setNotice(e?.message || String(e));
    } finally {
      setBusy(null);
    }
  }

  async function reset() {
    setBusy("reset");
    setNotice(null);
    try {
      const next = await adminAnswerPolicyApi.reset();
      setPolicy(next);
      setDraft(editable(next));
      setReason("");
      setNotice("已恢复系统默认回答策略，新请求开始生效。");
      setAudit((await adminAnswerPolicyApi.audit()).entries);
    } catch (e: any) {
      setNotice(e?.message || String(e));
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <LoadingState className="min-h-72" label="正在加载回答策略…" />;
  if (error || !policy) return <ErrorState title="回答策略加载失败" description={error || "暂无可用策略"} action={<Button variant="outline" onClick={() => void load()}>重试</Button>} />;

  return (
    <div className="space-y-6" aria-labelledby="answer-policy-title">
      <div>
        <p className="text-ui-xs font-medium text-primary">总览 / 系统管理员</p>
        <h1 id="answer-policy-title" className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">回答策略</h1>
        <p className="mt-2 max-w-2xl text-ui-sm text-muted-foreground">统一调整回答长度、上下文范围和低相关性保护。策略按请求读取，保存后新请求生效。</p>
      </div>

      {notice && <Alert variant={notice.includes("失败") ? "destructive" : "success"} aria-live="polite"><AlertTitle>操作结果</AlertTitle><AlertDescription>{notice}</AlertDescription></Alert>}

      <Card>
        <CardHeader><CardTitle>回答生成</CardTitle><CardDescription>控制模型输出的稳定性和最大长度。数值越大不代表回答一定更长。</CardDescription></CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-3">
          <label className="space-y-1.5 text-ui-sm font-medium"><span>回答温度</span><Input type="number" min={0} max={1} step={0.05} value={draft.answer_temperature} onChange={(e) => setDraft({ ...draft, answer_temperature: Number(e.target.value) })} /><span className="block text-ui-xs font-normal text-muted-foreground">范围 0 至 1，默认 0.2。</span></label>
          <label className="space-y-1.5 text-ui-sm font-medium"><span>最大输出 Token</span><Input type="number" min={256} max={4096} step={64} value={draft.answer_max_output_tokens} onChange={(e) => setDraft({ ...draft, answer_max_output_tokens: Number(e.target.value) })} /><span className="block text-ui-xs font-normal text-muted-foreground">范围 256 至 4096。</span></label>
          <label className="space-y-1.5 text-ui-sm font-medium"><span>上下文字符上限</span><Input type="number" min={2000} max={12000} step={500} value={draft.answer_context_chars} onChange={(e) => setDraft({ ...draft, answer_context_chars: Number(e.target.value) })} /><span className="block text-ui-xs font-normal text-muted-foreground">范围 2000 至 12000，包含检索资料预算。</span></label>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>相关性保护</CardTitle><CardDescription>默认关闭。开启前先完成阈值校准，低于阈值时系统会明确拒答而不会编造答案。</CardDescription></CardHeader>
        <CardContent className="space-y-5">
          <label className="flex items-start gap-3 rounded-ui-lg border border-border p-4"><Checkbox checked={draft.relevance_gate_enabled} onChange={(e) => setDraft({ ...draft, relevance_gate_enabled: e.target.checked })} disabled={busy !== null} /><span><span className="block text-ui-sm font-medium">启用低相关性回答拦截</span><span className="mt-1 block text-ui-xs text-muted-foreground">只影响满足门禁资格的单轮检索请求，不改变查询守卫。</span></span></label>
          <div className="grid gap-4 sm:grid-cols-3">
            <label className="space-y-1.5 text-ui-sm font-medium"><span>最低重排分数</span><Input type="number" min={0} step={0.01} value={draft.relevance_min_score} onChange={(e) => setDraft({ ...draft, relevance_min_score: Number(e.target.value) })} /></label>
            <label className="space-y-1.5 text-ui-sm font-medium"><span>最低 RRF 分数</span><Input type="number" min={0} step={0.0001} value={draft.relevance_min_rrf} onChange={(e) => setDraft({ ...draft, relevance_min_rrf: Number(e.target.value) })} /></label>
            <label className="space-y-1.5 text-ui-sm font-medium"><span>最低分数差</span><Input type="number" min={0} step={0.01} value={draft.relevance_min_margin} onChange={(e) => setDraft({ ...draft, relevance_min_margin: Number(e.target.value) })} /></label>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-ui-xs text-muted-foreground">当前策略版本：<Badge variant="outline">{policy.policy_version}</Badge></div>
        </CardContent>
      </Card>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><p className="text-ui-xs text-muted-foreground">系统查询守卫始终启用，裸数字问题会要求补充上下文。</p><div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => void reset()} disabled={busy !== null}><RotateCcw className="size-4" />{busy === "reset" ? "恢复中…" : "恢复默认"}</Button><Button onClick={() => void save()} disabled={!dirty || busy !== null}><Save className="size-4" />{busy === "save" ? "保存中…" : "保存策略"}</Button></div></div>

      <Card>
        <CardHeader><CardTitle>策略审计记录</CardTitle><CardDescription>记录每次保存和恢复默认的前后策略，便于线上问题排查和回答版本核对。</CardDescription></CardHeader>
        <CardContent>{audit.length === 0 ? <p className="text-ui-sm text-muted-foreground">暂无策略变更记录。</p> : <div className="space-y-3">{audit.map((entry) => <div key={entry.id} className="border-b border-border pb-3 last:border-b-0 last:pb-0"><div className="flex flex-wrap items-center justify-between gap-2"><span className="text-ui-sm font-medium">{entry.change_reason || "策略调整"}</span><time className="text-ui-xs text-muted-foreground">{formatAdminDate(entry.created_at)}</time></div><p className="mt-1 text-ui-xs text-muted-foreground">操作者：{entry.changed_by_name || "已离职用户"} · 输出 Token {changedVersion(entry, "answer_max_output_tokens")} · 上下文 {changedVersion(entry, "answer_context_chars")} · 门禁 {changedVersion(entry, "relevance_gate_enabled")}</p></div>)}</div>}</CardContent>
      </Card>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}><DialogContent><DialogHeader><DialogTitle>确认开启相关性保护</DialogTitle><DialogDescription>开启后，部分低相关性问题会直接返回补充资料提示。请填写变更原因，方便之后审计。</DialogDescription></DialogHeader><label className="space-y-1.5 text-ui-sm font-medium"><span>变更原因</span><Input autoFocus value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} placeholder="例如：完成线上误答问题的阈值校准" /></label><DialogFooter><Button variant="ghost" onClick={() => setConfirmOpen(false)}>取消</Button><Button onClick={() => void save()} disabled={!reason.trim() || busy !== null}>确认开启并保存</Button></DialogFooter></DialogContent></Dialog>
    </div>
  );
}
