import { describe, expect, it } from "vitest";
import type { Source } from "../types";
import {
  formatSectionPath,
  formatSourcesAsMarkdown,
  sourceCategoryLabel,
  sourceDisplayTitle,
  sourceLocator,
} from "./source-export";

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
  it("turns internal and missing section values into user-facing locators", () => {
    expect(formatSectionPath("(intro)")).toBe("文档开头");
    expect(formatSectionPath("<b>内部</b> > 章节一")).toBe("内部 / 章节一");
    expect(sourceLocator({ ...source, doc_type: "pdf", section_path: "" })).toBe("未提供定位信息");
    expect(sourceLocator({ ...source, doc_type: "pdf", section_path: "(intro)" })).toBe("文档开头");
  });

  it("uses the same display title and document-specific locator as the workspace", () => {
    expect(sourceDisplayTitle(source)).toBe("培训视频");
    expect(sourceLocator({ ...source, doc_type: "xlsx", sheet_name: "统计表", cell_range: "B2:F20" })).toBe("统计表 · B2:F20");
    expect(sourceLocator({ ...source, doc_type: "pptx", slide_number: 8 })).toBe("第 8 页");
    expect(sourceLocator({ ...source, doc_type: "docx", paragraph_anchor: "第 3.2 节" })).toBe("第 3.2 节");
    expect(sourceLocator({ ...source, doc_type: "xlsx", sheet_name: null, cell_range: null })).toBe("内部 / 章节一");
  });

  it("adds a distinct company to the category without duplicating labels", () => {
    expect(sourceCategoryLabel({ ...source, category: "公司内部标准", company: "品茗股份" })).toBe("公司内部标准 · 品茗股份");
    expect(sourceCategoryLabel({ ...source, category: "公司内部标准", company: "公司内部标准" })).toBe("公司内部标准");
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

  it("exports the same company and intro labels shown in the workspace", () => {
    const markdown = formatSourcesAsMarkdown([{
      ...source,
      doc_type: "pdf",
      section_path: "(intro)",
      category: "公司内部标准",
      company: "品茗股份",
    }]);

    expect(markdown).toContain("- 分类：公司内部标准 · 品茗股份");
    expect(markdown).toContain("- 定位：文档开头");
  });
});
