// Citation linker: turn inline `[doc §section]` / `[doc @HH:MM:SS]` markers
// produced by the LLM into markdown links with a sentinel `#cite-…` href.
// react-markdown renders them as <a>; Message.tsx intercepts the click and
// dispatches a CITATION_EVENT so the matching SourcesPanel can open + scroll.

import type { Source } from "../types";

export const CITATION_EVENT = "pincheng:citation-click";
// Sent from SourcesPanel when hovering a source card to highlight the citation in-message.
export const CITATION_HOVER_EVENT = "pincheng:citation-hover";
// Sent while an inline citation tooltip is active so streaming auto-follow does not move it.
export const CITATION_TOOLTIP_ACTIVE_EVENT = "pincheng:citation-tooltip-active";

export type CitationDetail = {
  messageId: string;
  sourceIndex: number;
};

export type CitationSelection = CitationDetail | null;

export type CitationHoverDetail = {
  messageId: string;
  sourceIndex: number | null; // null = clear highlight
};

export type CitationTooltipActiveDetail = {
  markerId: string;
  active: boolean;
};

export function toggleCitationSelection(
  current: CitationSelection,
  clicked: CitationDetail,
): CitationSelection {
  return current?.messageId === clicked.messageId &&
    current.sourceIndex === clicked.sourceIndex
    ? null
    : clicked;
}

// Group 1 = doc title, Group 2 = section path. Negative lookahead avoids
// eating real markdown links `[label](url)`. Both bracketed and bare forms
// are matched — the LLM occasionally omits the `[...]` wrapper.
const PDF_RE = /(?:\[([^\]]+?)\]|([^\[\]\n§]+?))\s*§\s*([^\[\]\n]+?)(?!\()/g;
// Video citations: [doc @HH:MM[:SS]] or doc @HH:MM[:SS] (without brackets)
const VID_RE = /(?:\[([^\]]+?)\]|([^\[\]\n@]+?))\s*@\s*(\d{1,2}:\d{2}(?::\d{2})?)(?!\()/g;

export function linkifyCitations(markdown: string): string {
  // First pass: numbered references [N] or [Ntext] → [N](#cite-num:N)
  // The LLM sometimes outputs [1专业项目文件、中心文件命名] instead of just [1].
  let result = markdown.replace(
    /\[(\d+)[^\]]*?\](?!\()/g,
    (m, num) => `[${num}](#cite-num:${num})`,
  );
  // Second pass: also handle [doc @time] and [doc §section] patterns
  result = result
    .replace(VID_RE, (m, bracketed, unbracketed, time) => {
      const doc = (bracketed || unbracketed || "").trim();
      if (!doc) return m;
      const href = `#cite-vid:${encodeURIComponent(doc)}::${encodeURIComponent(time.trim())}`;
      return `[${doc}](${href})`;
    })
    .replace(PDF_RE, (m, bracketed, unbracketed, section) => {
      const doc = (bracketed || unbracketed || "").trim();
      if (!doc) return m;
      const href = `#cite-pdf:${encodeURIComponent(doc)}::${encodeURIComponent(section.trim())}`;
      return `[${doc} § ${section.trim()}](${href})`;
    });
  return result;
}

export function stripCitationsForCopy(markdown: string): string {
  return linkifyCitations(markdown).replace(
    /[ \t]*\[[^\]\n]*\]\(#cite-(?:num|vid|pdf):[^\n]*?\)(?=$|[\s，。！？；：,.!?;:])/g,
    "",
  );
}

export function resolveCitation(href: string, sources: Source[]): number {
  // Numbered reference: #cite-num:N → source index N-1
  const numMatch = href.match(/^#cite-num:(\d+)$/);
  if (numMatch) {
    const idx = parseInt(numMatch[1], 10) - 1;
    return idx >= 0 && idx < sources.length ? idx : -1;
  }
  // Legacy: href looks like `#cite-pdf:<doc>::<section>` or `#cite-vid:<doc>::<time>`.
  const m = href.match(/^#cite-(pdf|vid):(.+?)::(.+)$/);
  if (!m) return -1;
  const [, kind, encDoc, encTail] = m;
  const doc = decodeURIComponent(encDoc);
  const tail = decodeURIComponent(encTail);
  // Exact match first.
  let idx = sources.findIndex((s) => {
    if (s.doc_title !== doc) return false;
    if (kind === "vid") return (s.start_time || "") === tail;
    return (s.section_path || "") === tail;
  });
  if (idx >= 0) return idx;
  // Leaf match: the LLM is instructed to cite the leaf of the breadcrumb
  // (e.g. `(5) 钢材耐腐蚀性差`) while `section_path` stores the full path
  // (`第1章 概述 > 1.1 ... > 1.1.1 ... > (5) 钢材耐腐蚀性差`). Match by the
  // trailing segment so short citations still resolve.
  if (kind === "pdf") {
    idx = sources.findIndex(
      (s) =>
        s.doc_title === doc &&
        ((s.section_path || "").split(" > ").pop() || "") === tail,
    );
    if (idx >= 0) return idx;
  }
  // Lenient: prefix match on section (LLM occasionally truncates).
  if (kind === "pdf") {
    idx = sources.findIndex(
      (s) => s.doc_title === doc && (s.section_path || "").startsWith(tail.split(/\s/)[0]),
    );
    if (idx >= 0) return idx;
  }
  // Fallback to doc-title-only match.
  return sources.findIndex((s) => s.doc_title === doc);
}

export function dispatchCitation(detail: CitationDetail) {
  window.dispatchEvent(new CustomEvent<CitationDetail>(CITATION_EVENT, { detail }));
}
