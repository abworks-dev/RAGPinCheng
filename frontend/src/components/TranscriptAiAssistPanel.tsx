import { useEffect, useMemo, useRef, useState } from "react";
import { Bot, Check, RefreshCw, Sparkles, X } from "lucide-react";
import { adminMediaApi } from "../api/admin/media";
import { Alert, AlertDescription, AlertTitle } from "./ui/alert";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Input } from "./ui/input";
import { LoadingState } from "./ui/loading-state";
import type { TranscriptAiHistoryItem, TranscriptReviewSuggestion, TranscriptSummaryResult } from "../types";

type PanelProps = {
  mediaTitle: string;
  versionId: string;
  markdown: string;
  baseMarkdownSha256: string;
  onAccept: (appliedMarkdown: string) => Promise<void>;
  onStale: () => void;
};

function confidenceLabel(level: string) {
  return ({ high: "高置信", medium: "中置信", low: "低置信" } as Record<string, string>)[level] ?? level;
}

export function TranscriptAiAssistPanel({ mediaTitle, versionId, markdown, baseMarkdownSha256, onAccept, onStale }: PanelProps) {
  const [summary, setSummary] = useState<TranscriptSummaryResult | null>(null);
  const [suggestions, setSuggestions] = useState<TranscriptReviewSuggestion[] | null>(null);
  const [summaryBusy, setSummaryBusy] = useState(false);
  const [suggestionBusy, setSuggestionBusy] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [summaryHistory, setSummaryHistory] = useState<TranscriptAiHistoryItem[]>([]);
  const [suggestionHistory, setSuggestionHistory] = useState<TranscriptAiHistoryItem[]>([]);
  const [correction, setCorrection] = useState("");
  const [selected, setSelected] = useState<Record<number, boolean>>({});
  const [appliedPreview, setAppliedPreview] = useState<string | null>(null);
  const acceptBusyRef = useRef(false);

  const versionRef = useRef(versionId);
  const markdownRef = useRef(markdown);
  const baseShaRef = useRef(baseMarkdownSha256);
  versionRef.current = versionId;
  markdownRef.current = markdown;
  baseShaRef.current = baseMarkdownSha256;

  useEffect(() => {
    setSummary(null);
    setSuggestions(null);
    setError(null);
    setStale(false);
    setSummaryHistory([]);
    setSuggestionHistory([]);
    setCorrection("");
    setSelected({});
    setAppliedPreview(null);
  }, [versionId]);

  // Mark stale when the underlying transcript changed (new hash).
  useEffect(() => {
    if (baseShaRef.current !== baseMarkdownSha256) return;
    // Base-hash change only detectable when version switches; handled by useEffect above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseMarkdownSha256]);

  function handleApiError(cause: unknown): boolean {
    const message = cause instanceof Error ? cause.message : String(cause);
    if (/已更新|version_stale|409/.test(message)) {
      setStale(true);
      onStale();
      setError("转录稿已更新，请基于最新版本重新生成。");
      return true;
    }
    setError(message || "操作失败，请稍后重试");
    return false;
  }

  async function runSummary(history: TranscriptAiHistoryItem[]) {
    setSummaryBusy(true);
    setError(null);
    try {
      const result = await adminMediaApi.transcriptSummary(versionId, baseMarkdownSha256, history);
      setSummary(result);
      setSummaryHistory(history);
    } catch (cause) {
      if (!handleApiError(cause)) setSummaryHistory([]);
    } finally {
      setSummaryBusy(false);
    }
  }

  async function runSuggestions(history: TranscriptAiHistoryItem[]) {
    setSuggestionBusy(true);
    setError(null);
    try {
      const result = await adminMediaApi.transcriptReviewSuggestions(versionId, baseMarkdownSha256, history);
      setSuggestions(result.suggestions);
      setSuggestionHistory(history);
      setSelected(Object.fromEntries(result.suggestions.map((_, index) => [index, result.suggestions[index].confidence === "high"])));
    } catch (cause) {
      if (!handleApiError(cause)) setSuggestionHistory([]);
    } finally {
      setSuggestionBusy(false);
    }
  }

  async function sendCorrection(kind: "summary" | "review") {
    const text = correction.trim();
    if (!text) return;
    setCorrection("");
    if (kind === "summary") {
      const history = [...summaryHistory, { role: "user" as const, content: text }];
      setSummaryHistory(history);
      await runSummary(history);
    } else {
      const history = [...suggestionHistory, { role: "user" as const, content: text }];
      setSuggestionHistory(history);
      await runSuggestions(history);
    }
  }

  function applySelected() {
    if (!suggestions) return;
    const picked = suggestions.filter((_, index) => selected[index]);
    if (picked.length === 0) {
      setAppliedPreview(null);
      return;
    }
    // Splice into the current markdown (same logic as backend): replace original
    // within the matching 说话人 timestamp turn, first occurrence per suggestion.
    let current = markdown;
    for (const suggestion of picked) {
      if (!suggestion.original || !suggestion.corrected || suggestion.original === suggestion.corrected) continue;
      const marker = new RegExp(`^#*\\s*说话[人⼈]\\s+\\d+\\s+${escapeRegExp(suggestion.timestamp)}\\s*$`, "m");
      const turnMatch = marker.exec(current);
      if (!turnMatch) continue;
      const bodyStart = turnMatch.index + turnMatch[0].length;
      const nextMarker = current.slice(bodyStart).search(/^#*\s*说话[人⼈]\s+\d+\s+\d{1,2}:\d{2}(?::\d{2})?\s*$/m);
      const bodyEnd = nextMarker === -1 ? current.length : bodyStart + nextMarker;
      const body = current.slice(bodyStart, bodyEnd);
      const pos = body.indexOf(suggestion.original);
      if (pos === -1) continue;
      current = current.slice(0, bodyStart + pos) + suggestion.corrected + current.slice(bodyStart + pos + suggestion.original.length);
    }
    setAppliedPreview(current);
  }

  async function acceptApplied() {
    if (!appliedPreview || acceptBusyRef.current) return;
    acceptBusyRef.current = true;
    setApplyBusy(true);
    setError(null);
    try {
      await onAccept(appliedPreview);
      setAppliedPreview(null);
      setSuggestions(null);
      setSummary(null);
    } catch (cause) {
      handleApiError(cause);
    } finally {
      acceptBusyRef.current = false;
      setApplyBusy(false);
    }
  }

  const hasContent = Boolean(summary || suggestions);

  return (
    <section className="mt-4 space-y-3 rounded-ui-md border border-border bg-surface-muted/40 p-3" aria-label="AI 转录稿助手">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h5 className="flex items-center gap-1.5 text-ui-xs font-medium text-foreground"><Sparkles className="size-3.5 text-primary" />AI 转录稿助手</h5>
        {stale && <Badge variant="warning">转录稿已更新，需重新生成</Badge>}
      </div>

      {error && <Alert variant="destructive" role="alert"><AlertTitle>AI 助手</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}

      <div className="grid gap-3 lg:grid-cols-2">
        {/* ── 视频总结卡片 ── */}
        <div className="min-w-0 space-y-2 rounded-ui-md border border-border bg-background p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h6 className="flex items-center gap-1.5 text-ui-xs font-semibold"><Bot className="size-3.5" />视频总结</h6>
            <div className="flex items-center gap-1.5">
              <Button size="sm" variant="ghost" disabled={summaryBusy} onClick={() => void runSummary([])}><RefreshCw className="size-3.5" />重新生成</Button>
            </div>
          </div>
          {summaryBusy ? <LoadingState className="min-h-24" label="正在生成总结…" /> : summary ? (
            <div className="space-y-2">
              <ul className="space-y-1 text-ui-xs">
                {summary.points.map((point, index) => <li key={index} className="flex gap-2"><span className="shrink-0 font-medium text-muted-foreground">{index + 1}.</span><span>{point}</span></li>)}
              </ul>
              {summary.mismatch_note && <p className="text-ui-xs text-warning">⚠ {summary.mismatch_note}</p>}
              {summaryHistory.length > 0 && <p className="text-ui-[11px] text-muted-foreground">已按 {summaryHistory.length} 条指正重新生成</p>}
            </div>
          ) : <p className="text-ui-xs text-muted-foreground">暂无总结。点击重新生成为当前转录版本生成 AI 总结。</p>}

          {summary && (
            <form className="flex items-center gap-2" onSubmit={(event) => { event.preventDefault(); void sendCorrection("summary"); }}>
              <Input aria-label="指正视频总结" placeholder="指正：这段总结不准确…" value={correction} disabled={summaryBusy} onChange={(event) => setCorrection(event.target.value)} />
              <Button type="submit" size="sm" variant="outline" disabled={summaryBusy || !correction.trim()}>指正</Button>
            </form>
          )}
        </div>

        {/* ── 修正建议 ── */}
        <div className="min-w-0 space-y-2 rounded-ui-md border border-border bg-background p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h6 className="flex items-center gap-1.5 text-ui-xs font-semibold"><Check className="size-3.5" />修正建议</h6>
            <div className="flex items-center gap-1.5">
              <Button size="sm" variant="ghost" disabled={suggestionBusy} onClick={() => void runSuggestions([])}><RefreshCw className="size-3.5" />生成建议</Button>
            </div>
          </div>
          {suggestionBusy ? <LoadingState className="min-h-24" label="正在生成修正建议…" /> : suggestions ? (
            <div className="space-y-2">
              {suggestions.length === 0
                ? <p className="text-ui-xs text-muted-foreground">未发现值得修正的片段，或均为低置信存疑项。</p>
                : <ul className="max-h-56 space-y-2 overflow-y-auto pr-1 text-ui-xs">
                    {suggestions.map((suggestion, index) => (
                      <li key={index} className="space-y-1 rounded-ui-md border border-border p-2">
                        <label className="flex items-start gap-2">
                          <Checkbox checked={Boolean(selected[index])} onChange={(event) => setSelected((current) => ({ ...current, [index]: event.target.checked }))} />
                          <span className="min-w-0 flex-1">
                            <span className="flex flex-wrap items-center gap-1.5">
                              <code className="rounded-ui-sm bg-surface-muted px-1 py-0.5 font-mono">{suggestion.timestamp}</code>
                              <Badge variant={suggestion.confidence === "high" ? "success" : suggestion.confidence === "medium" ? "warning" : "secondary"}>{confidenceLabel(suggestion.confidence)}</Badge>
                            </span>
                            <span className="mt-1 block"><s className="break-words text-muted-foreground">{suggestion.original}</s></span>
                            <span className="mt-0.5 block break-words text-foreground">{suggestion.corrected || "（删除）"}</span>
                            {suggestion.reason && <span className="mt-1 block text-[11px] text-muted-foreground">{suggestion.reason}</span>}
                          </span>
                        </label>
                      </li>
                    ))}
                  </ul>}
              <div className="flex flex-wrap items-center gap-1.5">
                <Button size="sm" variant="outline" disabled={!suggestions.length} onClick={applySelected}>应用勾选</Button>
                <Button size="sm" variant="outline" disabled={!suggestions.length} onClick={() => setSelected(Object.fromEntries(suggestions.map((_, index) => [index, true])))}>全选</Button>
              </div>
              {appliedPreview && (
                <div className="space-y-2">
                  <p className="text-ui-[11px] text-muted-foreground">预览应用后的转录稿（保存即创建新草稿，时间戳与说话人行不变）。</p>
                  <pre className="max-h-40 overflow-y-auto rounded-ui-md border border-border bg-surface-muted/60 p-2 text-[11px] leading-relaxed">{appliedPreview}</pre>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Button size="sm" disabled={applyBusy} onClick={() => void acceptApplied()}>{applyBusy ? "保存中…" : "接受并保存为新草稿"}</Button>
                    <Button size="sm" variant="ghost" onClick={() => setAppliedPreview(null)}>取消预览</Button>
                  </div>
                </div>
              )}
              {suggestionHistory.length > 0 && <p className="text-ui-[11px] text-muted-foreground">已按 {suggestionHistory.length} 条指正重新生成</p>}
            </div>
          ) : <p className="text-ui-xs text-muted-foreground">暂无建议。点击生成修正建议按资料名称、总结与转录稿找出可修正片段。</p>}

          {suggestions && (
            <form className="flex items-center gap-2" onSubmit={(event) => { event.preventDefault(); void sendCorrection("review"); }}>
              <Input aria-label="指正修正建议" placeholder="指正：第 3 条改动过大，只改错别字…" value={correction} disabled={suggestionBusy} onChange={(event) => setCorrection(event.target.value)} />
              <Button type="submit" size="sm" variant="outline" disabled={suggestionBusy || !correction.trim()}>指正</Button>
            </form>
          )}
        </div>
      </div>
    </section>
  );
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}