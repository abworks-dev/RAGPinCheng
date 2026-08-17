import type { Source } from "../types";
import { stripMarkdown } from "../utils/markdown";

export function cleanSourceSection(source: Source): string {
  return (source.section_path || "")
    .replace(/<[^>]*>/g, "")
    .split(" > ")
    .filter(Boolean)
    .join(" / ");
}

export function sourceLocator(source: Source): string {
  if (source.doc_type === "transcript") return source.start_time || "未提供时间";
  if (source.doc_type === "xlsx" && (source.sheet_name || source.cell_range)) {
    return [source.sheet_name, source.cell_range].filter(Boolean).join(" · ");
  }
  if (source.doc_type === "pptx" && source.slide_number) return `第 ${source.slide_number} 页`;
  if (source.doc_type === "docx" && source.paragraph_anchor) return source.paragraph_anchor;
  return cleanSourceSection(source) || "未提供定位信息";
}

export function sourceDisplayTitle(source: Source): string {
  if (source.doc_type !== "transcript") return source.doc_title;
  return source.doc_title.replace(/__[0-9a-f]{8}$/i, "");
}

const sourceTypeLabels: Record<string, string> = {
  transcript: "视频转录",
  pdf: "PDF",
  docx: "Word",
  xlsx: "Excel",
  pptx: "PowerPoint",
};

export function formatSourcesAsMarkdown(sources: Source[]): string {
  const sections = sources.map((source, index) => {
    const text = stripMarkdown(source.text).trim();
    const quotedText = text
      ? text.split(/\r?\n/).map((line) => `> ${line}`).join("\n")
      : "> （无引用原文）";
    return [
      `## ${index + 1}. ${sourceDisplayTitle(source)}`,
      "",
      `- 类型：${sourceTypeLabels[source.doc_type] || source.doc_type}`,
      `- 分类：${source.category || "未分类"}`,
      `- 定位：${sourceLocator(source)}`,
      "",
      quotedText,
    ].join("\n");
  });
  return ["# 回答来源", "", ...sections].join("\n\n").trimEnd() + "\n";
}
