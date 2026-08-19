import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Conversation } from "../types";
import { ConversationList } from "./ConversationList";

function conversation(id: string, title: string, ageDays: number): Conversation {
  return {
    id,
    title,
    created_at: Date.now() / 1000 - ageDays * 86400,
    updated_at: Date.now() / 1000 - ageDays * 86400,
    turn_index: 1,
  };
}

describe("ConversationList date groups", () => {
  afterEach(() => vi.useRealTimers());

  it("groups conversations into the four recency ranges", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-03T10:00:00+08:00"));
    render(
      <ConversationList
        conversations={[
          conversation("today", "今日对话", 0),
          conversation("week", "本周对话", 3),
          conversation("month", "本月对话", 12),
          conversation("older", "更早对话", 45),
        ]}
        currentId={null}
        onSelect={() => {}}
        onDelete={() => {}}
        loading={false}
      />,
    );

    expect(screen.getByRole("heading", { name: "今天" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "7 天内" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "30 天内" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "更早" })).toBeInTheDocument();
    expect(screen.queryByText(/分钟前|小时前|天前/)).not.toBeInTheDocument();
  });

  it("confirms deletion in an in-page dialog", () => {
    const onDelete = vi.fn();
    const target = conversation("conversation-1", "季度复盘", 0);
    render(
      <ConversationList
        conversations={[target]}
        currentId={target.id}
        onSelect={vi.fn()}
        onDelete={onDelete}
        loading={false}
      />,
    );

    fireEvent.click(screen.getByTitle("删除对话"));

    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("删除对话“季度复盘”？此操作不可恢复。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onDelete).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTitle("删除对话"));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    expect(onDelete).toHaveBeenCalledOnce();
    expect(onDelete).toHaveBeenCalledWith(target.id);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
