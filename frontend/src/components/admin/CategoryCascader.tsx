import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, ChevronLeft, ChevronRight, Folder, Search } from "lucide-react";
import { buildCategoryTree, type CategoryTreeNode } from "../../lib/category-tree";
import type { ManagedCategory } from "../../types";
import { Button } from "../ui/button";
import { Input } from "../ui/input";

type CategoryCascaderProps = {
  categories: ManagedCategory[];
  value: string;
  onChange: (categoryId: string) => void;
  label?: string;
};

function categoryPathIds(categories: ManagedCategory[], categoryId: string) {
  const byId = new Map(categories.map((category) => [category.id, category]));
  const path: string[] = [];
  let current = byId.get(categoryId);
  while (current) {
    path.unshift(current.id);
    current = current.parent_id ? byId.get(current.parent_id) : undefined;
  }
  return path;
}

function findTreeNode(nodes: CategoryTreeNode[], categoryId: string): CategoryTreeNode | null {
  for (const node of nodes) {
    if (node.category.id === categoryId) return node;
    const child = findTreeNode(node.children, categoryId);
    if (child) return child;
  }
  return null;
}

function nodesBelowPath(tree: CategoryTreeNode[], path: string[]) {
  let nodes = tree;
  for (const categoryId of path) {
    const node = nodes.find((entry) => entry.category.id === categoryId);
    if (!node) return tree;
    nodes = node.children;
  }
  return nodes;
}

export function CategoryCascader({ categories, value, onChange, label = "目录" }: CategoryCascaderProps) {
  const labelId = useId();
  const panelId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [openUpward, setOpenUpward] = useState(false);
  const [query, setQuery] = useState("");
  const [candidateId, setCandidateId] = useState(value);
  const activeCategories = useMemo(() => categories.filter((category) => category.is_active && !(category.category_kind === "shared_folder" && Boolean(category.external_relative_path))), [categories]);
  const tree = useMemo(() => buildCategoryTree(activeCategories), [activeCategories]);
  const [activePath, setActivePath] = useState<string[]>(() => categoryPathIds(activeCategories, value));
  const selectedCategory = activeCategories.find((category) => category.id === value) || null;
  const candidateCategory = activeCategories.find((category) => category.id === candidateId) || null;
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const searchResults = useMemo(() => normalizedQuery
    ? activeCategories
      .filter((category) => [category.display_code, category.display_name, category.full_path]
        .some((field) => field.toLocaleLowerCase().includes(normalizedQuery)))
      .sort((left, right) => left.full_path.localeCompare(right.full_path, "zh-CN"))
    : [], [activeCategories, normalizedQuery]);

  const columns = useMemo(() => {
    const result: CategoryTreeNode[][] = [tree];
    let nodes = tree;
    for (const categoryId of activePath) {
      const node = nodes.find((entry) => entry.category.id === categoryId);
      if (!node || node.children.length === 0) break;
      nodes = node.children;
      result.push(nodes);
    }
    return result;
  }, [activePath, tree]);

  const lastActiveNode = activePath.length > 0 ? findTreeNode(tree, activePath.at(-1)!) : null;
  const mobileBrowsePath = lastActiveNode?.children.length ? activePath : activePath.slice(0, -1);
  const mobileParent = mobileBrowsePath.length > 0 ? findTreeNode(tree, mobileBrowsePath.at(-1)!) : null;
  const mobileNodes = nodesBelowPath(tree, mobileBrowsePath);

  useEffect(() => {
    if (open) return;
    setCandidateId(value);
    setActivePath(categoryPathIds(activeCategories, value));
    setQuery("");
  }, [activeCategories, open, value]);

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("mousedown", closeOutside);
    document.addEventListener("keydown", closeEscape);
    return () => {
      document.removeEventListener("mousedown", closeOutside);
      document.removeEventListener("keydown", closeEscape);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !triggerRef.current || !panelRef.current) return;
    const triggerRect = triggerRef.current.getBoundingClientRect();
    const panelHeight = panelRef.current.getBoundingClientRect().height;
    const spaceBelow = window.innerHeight - triggerRect.bottom - 12;
    const spaceAbove = triggerRect.top - 12;
    setOpenUpward(panelHeight > spaceBelow && spaceAbove > spaceBelow);
  }, [open, query, columns.length]);

  const stageCategory = (category: ManagedCategory, level?: number) => {
    setCandidateId(category.id);
    setActivePath((current) => level === undefined
      ? categoryPathIds(activeCategories, category.id)
      : [...current.slice(0, level), category.id]);
  };
  const chooseCategory = () => {
    if (!candidateId) return;
    onChange(candidateId);
    setOpen(false);
  };
  const clearCategory = () => {
    onChange("");
    setCandidateId("");
    setActivePath([]);
    setOpen(false);
  };

  const option = (node: CategoryTreeNode, scope: "desktop" | "mobile", level?: number) => {
    const selected = candidateId === node.category.id;
    return <button
      key={node.category.id}
      type="button"
      role="option"
      aria-selected={selected}
      data-testid={`category-cascader-${scope}-option-${node.category.id}`}
      title={node.category.full_path}
      onClick={() => stageCategory(node.category, level)}
      className={`flex min-h-10 w-full items-center gap-2 px-3 py-2 text-left text-ui-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${selected ? "bg-primary/10 text-primary" : "hover:bg-surface-muted"}`}
    >
      <Folder className="size-4 shrink-0" aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate">{node.category.display_code} {node.category.display_name}</span>
      {selected && <Check className="size-4 shrink-0" aria-label="已选择" />}
      {node.children.length > 0 && <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />}
    </button>;
  };

  return <div ref={rootRef} className="relative min-w-0 max-w-full space-y-1 text-ui-xs text-muted-foreground" role="group" aria-labelledby={labelId}>
    <span id={labelId}>{label}</span>
    <button
      ref={triggerRef}
      type="button"
      className="flex h-control-sm w-full items-center gap-2 rounded-ui-md border border-input bg-background px-3 text-left text-ui-sm text-foreground shadow-sm outline-none transition-colors hover:bg-surface-muted focus-visible:ring-2 focus-visible:ring-ring"
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-controls={panelId}
      onClick={() => setOpen((current) => !current)}
      title={selectedCategory?.full_path || "全部目录"}
    >
      <Folder className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate">{selectedCategory?.full_path || "全部目录"}</span>
      <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
    </button>
    {open && <div
      ref={panelRef}
      id={panelId}
      role="dialog"
      aria-modal="false"
      aria-label={`${label}级联选择`}
      className={`absolute inset-x-0 z-10 min-w-0 max-w-full overflow-hidden rounded-ui-md border border-border bg-popover text-popover-foreground shadow-overlay sm:left-auto sm:right-0 sm:w-[34rem] sm:max-w-[calc(100vw-2rem)] ${openUpward ? "bottom-full mb-2" : "top-full mt-2"}`}
    >
      <div className="border-b border-border p-3">
        <label className="relative block min-w-0">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <Input className="h-control-sm min-w-0 pl-9" type="search" value={query} onChange={(event) => setQuery(event.target.value)} aria-label="搜索原目录" placeholder="搜索编号、名称或完整路径" autoFocus />
        </label>
      </div>
      {normalizedQuery ? <div className="max-h-64 overflow-y-auto" role="listbox" aria-label="目录搜索结果">
        {searchResults.length > 0 ? searchResults.map((category) => <button
          key={category.id}
          type="button"
          role="option"
          aria-selected={candidateId === category.id}
          onClick={() => stageCategory(category)}
          className={`flex min-h-12 w-full items-start gap-2 border-b border-border px-3 py-2 text-left text-ui-sm outline-none last:border-b-0 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${candidateId === category.id ? "bg-primary/10 text-primary" : "hover:bg-surface-muted"}`}
        ><Folder className="mt-0.5 size-4 shrink-0" aria-hidden="true" /><span className="min-w-0 flex-1 break-words">{category.full_path}</span>{candidateId === category.id && <Check className="size-4 shrink-0" aria-label="已选择" />}</button>) : <p className="px-3 py-6 text-center text-ui-sm text-muted-foreground">没有匹配的目录</p>}
      </div> : <>
        <div className="hidden max-h-64 min-h-48 divide-x divide-border overflow-x-auto sm:flex" aria-label="目录级联列表">
          {columns.map((nodes, level) => <div key={level} className="min-w-44 flex-1 overflow-y-auto" role="listbox" aria-label={`第 ${level + 1} 级目录`}>{nodes.map((node) => option(node, "desktop", level))}</div>)}
        </div>
        <div className="sm:hidden">
          <div className="flex min-h-10 items-center gap-2 border-b border-border px-2">
            <button type="button" className="inline-flex size-8 items-center justify-center rounded-ui-sm disabled:opacity-30" aria-label="返回上一级目录" disabled={mobileBrowsePath.length === 0} onClick={() => setActivePath(mobileBrowsePath.slice(0, -1))}><ChevronLeft className="size-4" /></button>
            <span className="min-w-0 flex-1 truncate text-ui-sm font-medium">{mobileParent?.category.full_path || "全部目录"}</span>
          </div>
          <div className="max-h-56 overflow-y-auto" role="listbox" aria-label="当前级目录">{mobileNodes.map((node) => option(node, "mobile"))}</div>
        </div>
      </>}
      <div className="space-y-2 border-t border-border p-3">
        <p className="truncate text-ui-xs text-muted-foreground" title={candidateCategory?.full_path}>{candidateCategory ? `当前选择：${candidateCategory.full_path}` : "请选择目录"}</p>
        <div className="grid grid-cols-2 gap-2 sm:flex sm:items-center sm:justify-between">
          <Button className="min-w-0 w-full sm:w-auto" size="sm" variant="ghost" onClick={clearCategory}>全部目录</Button>
          <Button className="min-w-0 w-full sm:w-auto" size="sm" disabled={!candidateId} onClick={chooseCategory}>选择当前目录</Button>
        </div>
      </div>
    </div>}
  </div>;
}
