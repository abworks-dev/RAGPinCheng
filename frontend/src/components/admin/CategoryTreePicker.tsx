import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type MutableRefObject } from "react";
import { Check, ChevronDown, ChevronRight, Folder, Search } from "lucide-react";
import { Badge } from "../ui/badge";
import { EmptyState } from "../ui/empty-state";
import { Input } from "../ui/input";
import {
  buildCategoryTree,
  collectCategoryAncestorIds,
  collectExpandableCategoryIds,
  filterCategoryTree,
  flattenVisibleCategoryTree,
  type CategoryTreeNode,
} from "../../lib/category-tree";
import type { ManagedCategory } from "../../types";

type CategoryTreePickerProps = {
  categories: ManagedCategory[];
  value: string;
  onChange: (categoryId: string) => void;
  currentCategoryId?: string | null;
  currentCategorySelectable?: boolean;
  disabled?: boolean;
  label?: string;
  rootOption?: {
    value: string;
    label: string;
    description?: string;
    disabledReason?: string;
  };
  disabledCategoryReasons?: Record<string, string>;
};

export function CategoryTreePicker({
  categories,
  value,
  onChange,
  currentCategoryId = null,
  currentCategorySelectable = false,
  disabled = false,
  label = "目标目录",
  rootOption,
  disabledCategoryReasons = {},
}: CategoryTreePickerProps) {
  const labelId = useId();
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const nodeRefs = useRef(new Map<string, HTMLDivElement>());
  const rootRef = useRef<HTMLDivElement>(null);
  const activeCategories = useMemo(() => categories.filter((category) => category.is_active), [categories]);
  const tree = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return filterCategoryTree(
      buildCategoryTree(activeCategories),
      (category) => !normalizedQuery
        || [category.display_code, category.display_name, category.full_path]
          .some((valueToMatch) => valueToMatch.toLocaleLowerCase().includes(normalizedQuery)),
    );
  }, [activeCategories, query]);
  const visibleNodes = useMemo(() => flattenVisibleCategoryTree(tree, expanded), [expanded, tree]);
  const selectedCategory = activeCategories.find((category) => category.id === value) || null;
  const rootSelected = rootOption?.value === value;
  const rootVisible = Boolean(rootOption && !query.trim());
  const rootFocusable = rootVisible && !disabled && !rootOption?.disabledReason;
  const currentCategory = activeCategories.find((category) => category.id === currentCategoryId) || null;
  const focusableCategoryId = rootSelected && rootFocusable ? undefined : visibleNodes.some((node) => node.category.id === value)
    ? value
    : visibleNodes[0]?.category.id;

  useEffect(() => {
    if (!query.trim()) return;
    const matchingBranches = collectExpandableCategoryIds(tree);
    if (matchingBranches.length === 0) return;
    setExpanded((current) => new Set([...current, ...matchingBranches]));
  }, [query, tree]);

  useEffect(() => {
    if (!value) return;
    setExpanded((current) => new Set([
      ...current,
      ...collectCategoryAncestorIds(activeCategories, value),
    ]));
  }, [activeCategories, value]);

  const focusNode = (categoryId: string) => {
    nodeRefs.current.get(categoryId)?.focus();
  };

  const toggleExpanded = (categoryId: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(categoryId)) next.delete(categoryId);
      else next.add(categoryId);
      return next;
    });
  };

  return <div className="space-y-2" role="group" aria-labelledby={labelId}>
    <div className="flex items-center justify-between gap-3">
      <span id={labelId} className="text-ui-sm font-medium">{label}</span>
      <span className="text-ui-xs text-muted-foreground">{activeCategories.length} 个可用目录</span>
    </div>
    {currentCategory && <p className="break-words text-ui-xs text-muted-foreground">当前目录：{currentCategory.full_path}</p>}
    <label className="relative block">
      <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
      <Input
        type="search"
        className="pl-9"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="搜索编号、名称或完整路径"
        aria-label="搜索目标目录"
        disabled={disabled}
      />
    </label>
    <div className="max-h-[min(18rem,45vh)] overflow-y-auto rounded-ui-md border border-border bg-background" role="tree" aria-label="目标目录树" aria-disabled={disabled}>
      {rootOption && rootVisible && <div
        ref={rootRef}
        role="treeitem"
        aria-level={1}
        aria-selected={rootSelected}
        aria-disabled={disabled || Boolean(rootOption.disabledReason)}
        tabIndex={rootSelected && rootFocusable ? 0 : -1}
        title={rootOption.disabledReason}
        onClick={() => { if (!disabled && !rootOption.disabledReason) onChange(rootOption.value); }}
        onKeyDown={(event) => {
          if ((event.key === "Enter" || event.key === " ") && !disabled && !rootOption.disabledReason) {
            event.preventDefault();
            onChange(rootOption.value);
          }
          if (event.key === "ArrowDown" && visibleNodes[0]) {
            event.preventDefault();
            focusNode(visibleNodes[0].category.id);
          }
        }}
        className={`flex min-h-14 items-start gap-2 border-b border-l-2 border-border py-3 pl-3 pr-3 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${rootSelected ? "border-l-primary bg-primary/10" : rootOption.disabledReason ? "cursor-not-allowed border-l-transparent bg-surface-muted/50 opacity-70" : "cursor-pointer border-l-transparent hover:bg-surface-muted/60"}`}
      >
        <span className="flex size-6 shrink-0 items-center justify-center text-ui-sm font-semibold text-primary" aria-hidden="true">/</span>
        <span className="min-w-0 flex-1"><span className="flex items-center gap-2 font-semibold">{rootOption.label}{rootSelected && <Check className="size-4 text-primary" aria-label="已选择" />}</span><span className="mt-1 block break-words text-ui-xs text-muted-foreground">{rootOption.disabledReason || rootOption.description || "一级目录所在位置"}</span></span>
      </div>}
      {activeCategories.length === 0 ? <EmptyState className="rounded-none border-0" title="暂无可用目录" description="请先启用至少一个目录。" /> : tree.length === 0 ? <EmptyState className="rounded-none border-0" title="没有匹配的目录" description="请调整搜索关键词。" /> : tree.map((node, index) => (
        <CategoryTreePickerNode
          key={node.category.id}
          node={node}
          level={1}
          index={index}
          siblingCount={tree.length}
          selectedId={value}
          focusableCategoryId={focusableCategoryId}
          currentCategoryId={currentCategoryId}
          currentCategorySelectable={currentCategorySelectable}
          disabled={disabled}
          expanded={expanded}
          visibleNodes={visibleNodes}
          nodeRefs={nodeRefs}
          rootAvailable={rootFocusable}
          onFocusRoot={() => rootRef.current?.focus()}
          disabledCategoryReasons={disabledCategoryReasons}
          onSelect={onChange}
          onToggle={toggleExpanded}
          onFocusNode={focusNode}
        />
      ))}
    </div>
    {rootSelected ? <p className="flex items-start gap-1.5 text-ui-xs text-primary" role="status" aria-live="polite"><Check className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />已选择：{rootOption?.label}</p> : selectedCategory ? <p className="flex items-start gap-1.5 break-words text-ui-xs text-primary" role="status" aria-live="polite"><Check className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />已选择：{selectedCategory.full_path}</p> : <p className="text-ui-xs text-muted-foreground" role="status" aria-live="polite">请选择一个目标目录。</p>}
  </div>;
}

function CategoryTreePickerNode({
  node,
  level,
  index,
  siblingCount,
  selectedId,
  focusableCategoryId,
  currentCategoryId,
  currentCategorySelectable,
  disabled,
  expanded,
  visibleNodes,
  nodeRefs,
  rootAvailable,
  onFocusRoot,
  disabledCategoryReasons,
  onSelect,
  onToggle,
  onFocusNode,
}: {
  node: CategoryTreeNode;
  level: number;
  index: number;
  siblingCount: number;
  selectedId: string;
  focusableCategoryId: string | undefined;
  currentCategoryId: string | null;
  currentCategorySelectable: boolean;
  disabled: boolean;
  expanded: Set<string>;
  visibleNodes: CategoryTreeNode[];
  nodeRefs: MutableRefObject<Map<string, HTMLDivElement>>;
  rootAvailable: boolean;
  onFocusRoot: () => void;
  disabledCategoryReasons: Record<string, string>;
  onSelect: (categoryId: string) => void;
  onToggle: (categoryId: string) => void;
  onFocusNode: (categoryId: string) => void;
}) {
  const { category, children } = node;
  const isExpanded = expanded.has(category.id);
  const hasChildren = children.length > 0;
  const isCurrent = category.id === currentCategoryId;
  const isSelected = selectedId === category.id;
  const disabledReason = disabledCategoryReasons[category.id];
  const categoryDisabled = disabled || (isCurrent && !currentCategorySelectable) || Boolean(disabledReason);
  const visibleIndex = visibleNodes.findIndex((item) => item.category.id === category.id);
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const currentIndex = visibleIndex < 0 ? index : visibleIndex;
    const previous = visibleNodes[Math.max(0, currentIndex - 1)]?.category.id;
    const next = visibleNodes[Math.min(visibleNodes.length - 1, currentIndex + 1)]?.category.id;
    if (event.key === "ArrowDown" && next) { event.preventDefault(); onFocusNode(next); }
    if (event.key === "ArrowUp" && previous) { event.preventDefault(); onFocusNode(previous); }
    else if (event.key === "ArrowUp" && currentIndex === 0 && rootAvailable) { event.preventDefault(); onFocusRoot(); }
    if (event.key === "Home" && rootAvailable) { event.preventDefault(); onFocusRoot(); }
    else if (event.key === "Home" && visibleNodes[0]) { event.preventDefault(); onFocusNode(visibleNodes[0].category.id); }
    if (event.key === "End" && visibleNodes.at(-1)) { event.preventDefault(); onFocusNode(visibleNodes.at(-1)!.category.id); }
    if (event.key === "ArrowRight" && hasChildren && !disabled) {
      event.preventDefault();
      if (!isExpanded) onToggle(category.id);
      else onFocusNode(children[0].category.id);
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (isExpanded && !disabled) onToggle(category.id);
      else if (category.parent_id) onFocusNode(category.parent_id);
    }
    if ((event.key === "Enter" || event.key === " ") && !categoryDisabled) {
      event.preventDefault();
      onSelect(category.id);
    }
  };

  return <>
    <div
      ref={(element) => { if (element) nodeRefs.current.set(category.id, element); else nodeRefs.current.delete(category.id); }}
      role="treeitem"
      aria-level={level}
      aria-setsize={siblingCount}
      aria-posinset={index + 1}
      aria-expanded={hasChildren ? isExpanded : undefined}
      aria-selected={isSelected}
      aria-disabled={categoryDisabled}
      tabIndex={focusableCategoryId === category.id ? 0 : -1}
      data-testid={`category-picker-item-${category.id}`}
      title={disabledReason}
      onClick={() => { if (!categoryDisabled) onSelect(category.id); }}
      onKeyDown={onKeyDown}
      className={`relative flex min-h-14 items-start gap-2 border-l-2 py-3 pr-3 outline-none transition-colors duration-normal focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${level === 1 ? "pl-3" : "pl-2 before:absolute before:left-0 before:top-7 before:w-2 before:border-t before:border-border/70"} ${isSelected ? "border-l-primary bg-primary/10" : categoryDisabled ? "border-l-transparent bg-surface-muted/50" : "border-l-transparent hover:bg-surface-muted/60"} ${categoryDisabled ? "cursor-not-allowed opacity-70" : "cursor-pointer"}`}
    >
      <span className="flex size-6 shrink-0 items-center justify-center pt-0.5">
        {hasChildren ? <button type="button" aria-label={isExpanded ? `收起${category.display_name}` : `展开${category.display_name}`} title={isExpanded ? "收起" : "展开"} disabled={disabled} onClick={(event) => { event.stopPropagation(); onToggle(category.id); }} className="inline-flex size-6 items-center justify-center rounded-ui-sm text-muted-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed">{isExpanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}</button> : <span className="size-1.5 rounded-full bg-border" aria-hidden="true" />}
      </span>
      <Folder className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2"><span className={`break-words ${level === 1 ? "font-semibold" : "font-medium"}`}>{category.display_code} {category.display_name}</span>{isCurrent && <Badge variant="secondary">当前目录</Badge>}{isSelected && !isCurrent && <Check className="size-4 shrink-0 text-primary" aria-label="已选择" />}</span>
        <span className="mt-1 block break-words text-ui-xs text-muted-foreground">{disabledReason || `${category.item_count} 份直接资料${hasChildren ? ` · ${children.length} 个子目录` : ""}`}</span>
      </span>
    </div>
    {hasChildren && isExpanded && <div role="group" className="ml-5 border-l border-border/70 bg-surface-muted/10 sm:ml-6">{children.map((child, childIndex) => <CategoryTreePickerNode key={child.category.id} node={child} level={level + 1} index={childIndex} siblingCount={children.length} selectedId={selectedId} focusableCategoryId={focusableCategoryId} currentCategoryId={currentCategoryId} currentCategorySelectable={currentCategorySelectable} disabled={disabled} expanded={expanded} visibleNodes={visibleNodes} nodeRefs={nodeRefs} rootAvailable={rootAvailable} onFocusRoot={onFocusRoot} disabledCategoryReasons={disabledCategoryReasons} onSelect={onSelect} onToggle={onToggle} onFocusNode={onFocusNode} />)}</div>}
  </>;
}
