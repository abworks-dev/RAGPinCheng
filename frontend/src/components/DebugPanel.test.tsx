import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../types";
import { DebugPanel } from "./DebugPanel";

describe("DebugPanel citation diagnostics", () => {
  it("shows citation quality and invalid source numbers", () => {
    const msg: ChatMessage = {
      id: "answer-1",
      role: "assistant",
      content: "回答",
      done: {
        answer_text: "回答",
        timings: {},
        sources: [],
        history_chars: 0,
        budget: 1000,
        citation_diagnostics: {
          status: "invalid_citations",
          candidate_count: 4,
          cited_count: 1,
          located_count: 1,
          invalid_citation_numbers: [7],
          uncited_answer: false,
          uncited_statement_count: 0,
          citation_marker_count: 2,
          version_conflict: true,
        },
      },
    };

    render(<DebugPanel msg={msg} />);
    fireEvent.click(screen.getByRole("button", { name: "调试信息" }));

    expect(screen.getByText(/invalid_citations/)).toBeInTheDocument();
    expect(screen.getByText(/候选：/)).toHaveTextContent("4");
    expect(screen.getByText(/无效编号：/)).toHaveTextContent("7");
    expect(screen.getByText(/版本冲突：是/)).toBeInTheDocument();
  });
});
