import { describe, expect, it } from "vitest";
import { linkifyCitations, resolveCitation } from "./citations";
import type { Source } from "../types";

function source(overrides: Partial<Source> = {}): Source {
  return {
    parent_id: "p1",
    doc_title: "规范手册",
    section_path: "第1章 概述 > 1.1 总则",
    category: "公司标准",
    score: 0.9,
    rrf_score: 0.8,
    text: "正文",
    doc_type: "pdf",
    start_time: null,
    media_id: null,
    sheet_name: null,
    cell_range: null,
    slide_number: null,
    paragraph_anchor: null,
    ...overrides,
  };
}

function citation(kind: "pdf" | "vid", doc: string, tail: string) {
  return `#cite-${kind}:${encodeURIComponent(doc)}::${encodeURIComponent(tail)}`;
}

describe("citation parsing", () => {
  it("linkifies numbered, PDF section and video citations", () => {
    const result = linkifyCitations("见[1]、[规范手册 § 1.1 总则]和[培训视频 @01:02]");
    expect(result).toContain("[1](#cite-num:1)");
    expect(result).toContain("#cite-pdf:");
    expect(result).toContain("#cite-vid:");
  });

  it("resolves an exact section before same-title alternatives", () => {
    const sources = [
      source({ parent_id: "exact-other", section_path: "第1章 > 其他" }),
      source({ parent_id: "exact-target", section_path: "第1章 > 精确目标" }),
    ];

    expect(resolveCitation(citation("pdf", "规范手册", "第1章 > 精确目标"), sources)).toBe(1);
  });

  it("resolves a breadcrumb leaf before title fallback", () => {
    const sources = [
      source({ parent_id: "leaf-fallback", section_path: "第2章 > 其他" }),
      source({ parent_id: "leaf-target", section_path: "第2章 > 2.1 术语" }),
    ];

    expect(resolveCitation(citation("pdf", "规范手册", "2.1 术语"), sources)).toBe(1);
  });

  it("resolves a truncated section prefix before title fallback", () => {
    const sources = [
      source({ parent_id: "prefix-fallback", section_path: "附录 > 其他" }),
      source({ parent_id: "prefix-target", section_path: "第3章 概述 > 3.1 范围" }),
    ];

    expect(resolveCitation(citation("pdf", "规范手册", "第3章 摘要"), sources)).toBe(1);
  });

  it("falls back to the first matching document title", () => {
    const sources = [
      source({ parent_id: "fallback-target", section_path: "第4章 > 其他" }),
      source({ parent_id: "fallback-second", section_path: "第5章 > 其他" }),
    ];

    expect(resolveCitation(citation("pdf", "规范手册", "不存在的章节"), sources)).toBe(0);
  });

  it("resolves video timestamps before same-title fallback", () => {
    const sources = [
      source({ parent_id: "video-fallback", doc_title: "培训视频", doc_type: "transcript", section_path: "", start_time: "00:30" }),
      source({ parent_id: "video-target", doc_title: "培训视频", doc_type: "transcript", section_path: "", start_time: "01:02" }),
    ];

    expect(resolveCitation(citation("vid", "培训视频", "01:02"), sources)).toBe(1);
  });

  it("returns -1 for invalid or out-of-range references", () => {
    const sources = [source()];
    expect(resolveCitation("#cite-num:2", sources)).toBe(-1);
    expect(resolveCitation("#not-a-citation", sources)).toBe(-1);
  });
});
