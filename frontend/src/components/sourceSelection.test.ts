import { describe, expect, it } from "vitest";
import type { ChatMessage, Source } from "../types";
import { getSelectedSourceCount } from "./sourceSelection";

function sources(count: number): Source[] {
  return Array.from({ length: count }, (_, index) => ({
    parent_id: `doc-${index}`,
    doc_title: `来源 ${index + 1}`,
    section_path: "章节",
    category: "测试",
    text: `内容 ${index + 1}`,
    score: 1,
    rrf_score: 1,
    doc_type: "pdf",
    start_time: null,
    media_id: null,
    sheet_name: null,
    cell_range: null,
    slide_number: null,
    paragraph_anchor: null,
  }));
}

const messages: ChatMessage[] = [
  { id: "assistant-1", role: "assistant", content: "较早回答", sources: sources(7) },
  { id: "assistant-2", role: "assistant", content: "最新回答", sources: sources(3) },
];

describe("getSelectedSourceCount", () => {
  it("uses the source count of the currently selected answer", () => {
    expect(getSelectedSourceCount(messages, "assistant-1")).toBe(7);
    expect(getSelectedSourceCount(messages, "assistant-2")).toBe(3);
  });

  it("falls back to the latest sourced answer without a valid selection", () => {
    expect(getSelectedSourceCount(messages, null)).toBe(3);
    expect(getSelectedSourceCount(messages, "missing")).toBe(3);
  });
});
