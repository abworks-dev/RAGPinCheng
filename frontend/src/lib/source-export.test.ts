import { describe, expect, it } from "vitest";
import type { Source } from "../types";
import { formatSourcesAsMarkdown, sourceDisplayTitle, sourceLocator } from "./source-export";

const source: Source = {
  parent_id: "internal-parent-id",
  doc_title: "培训视频__7d44513f",
  doc_type: "transcript",
  section_path: "内部 > 章节一",
  text: "**引用原文**",
  category: "培训资料",
  score: 0.9,
  rrf_score: 0.8,
  start_time: "00:06:13",
  media_id: "internal-media-id",
  sheet_name: null,
  cell_range: null,
  slide_number: null,
  paragraph_anchor: null,
};

describe("source Markdown export", () => {
  it("uses the same display title and document-specific locator as the workspace", () => {
    expect(sourceDisplayTitle(source)).toBe("培训视频");
    expect(sourceLocator({ ...source, doc_type: "xlsx", sheet_name: "统计表", cell_range: "B2:F20" })).toBe("统计表 · B2:F20");
    expect(sourceLocator({ ...source, doc_type: "pptx", slide_number: 8 })).toBe("第 8 页");
    expect(sourceLocator({ ...source, doc_type: "docx", paragraph_anchor: "第 3.2 节" })).toBe("第 3.2 节");
  });

  it("preserves source order without exporting internal identifiers", () => {
    const markdown = formatSourcesAsMarkdown([
      source,
      { ...source, parent_id: "second-parent", doc_title: "第二份资料", doc_type: "pdf", text: "第二段" },
    ]);

    expect(markdown).toContain("## 1. 培训视频");
    expect(markdown).toContain("## 2. 第二份资料");
    expect(markdown).toContain("- 定位：00:06:13");
    expect(markdown).toContain("> 引用原文");
    expect(markdown).not.toContain("internal-parent-id");
    expect(markdown).not.toContain("internal-media-id");
    expect(markdown).not.toContain("7d44513f");
  });
});
