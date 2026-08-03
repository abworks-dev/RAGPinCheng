import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Sidebar } from "./Sidebar";

vi.mock("./ConversationList", () => ({
  ConversationList: () => <div>会话列表</div>,
}));

vi.mock("./UserMenu", () => ({
  UserMenu: () => <div>用户菜单</div>,
}));

function renderSidebar(collapsed: boolean, onToggleCollapsed = vi.fn()) {
  return render(
    <Sidebar
      conversations={[]}
      conversationsLoading={false}
      currentConversationId={null}
      onSelectConversation={() => {}}
      onDeleteConversation={() => {}}
      categories={[]}
      selected={[]}
      onToggle={() => {}}
      onClearCategories={() => {}}
      onNewChat={() => {}}
      collapsed={collapsed}
      onToggleCollapsed={onToggleCollapsed}
    />,
  );
}

describe("Sidebar brand", () => {
  it("shows the shared brand lockup in the expanded sidebar", () => {
    renderSidebar(false);

    expect(screen.getByText("品成 BIM 知识库")).toBeInTheDocument();
    expect(screen.getByText("知识问答工作台")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "收起会话侧栏" })).toBeInTheDocument();
  });

  it("uses the brand mark as the expand control in the collapsed sidebar", () => {
    const onToggleCollapsed = vi.fn();
    renderSidebar(true, onToggleCollapsed);

    expect(screen.queryByText("品成 BIM 知识库")).not.toBeInTheDocument();
    expect(screen.getByText("品")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开会话侧栏" }));
    expect(onToggleCollapsed).toHaveBeenCalledOnce();
  });
});
