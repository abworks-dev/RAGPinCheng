import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MutableRefObject, type ReactNode } from "react";
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  ChevronsDown,
  ChevronsUp,
  Folder,
  FolderOpen,
  FolderTree,
  GripVertical,
  Move,
  Plus,
  RefreshCw,
  Save,
  Search,
  TriangleAlert,
} from "lucide-react";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { adminContentApi } from "../../api/admin/content";
import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { IconButton } from "../../components/ui/icon-button";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import { Select } from "../../components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../../components/ui/sheet";
import { toast } from "../../components/ui/toast";
import type { ManagedCategory } from "../../types";
import {
  buildCategoryTree,
  compareManagedCategories,
  collectCategoryAncestorIds,
  collectExpandableCategoryIds,
  countCategoryTreeNodes,
  filterCategoryTree,
  flattenVisibleCategoryTree,
  type CategoryTreeNode,
} from "../../lib/category-tree";

type CategoryFilter = "all" | "active" | "inactive";
type CategoryDraft = Pick<ManagedCategory, "display_code" | "display_name" | "sort_order" | "is_active">;
type PendingAction =
  | { kind: "select"; id: string }
  | { kind: "refresh" }
  | { kind: "close" }
  | { kind: "create"; parentId: string | null }
  | null;
type CategoryMove = {
  categoryId: string;
  targetParentId: string | null;
  beforeCategoryId: string | null;
};

const EMPTY_DRAFT: CategoryDraft = { display_code: "", display_name: "", sort_order: 0, is_active: true };

function makeDraft(category: ManagedCategory): CategoryDraft {
  return {
    display_code: category.display_code,
    display_name: category.display_name,
    sort_order: category.sort_order,
    is_active: category.is_active,
  };
}

function filterTree(nodes: CategoryTreeNode[], query: string, filter: CategoryFilter): CategoryTreeNode[] {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const matches = (category: ManagedCategory) => {
    const queryMatch = !normalizedQuery
      || [category.display_code, category.display_name, category.full_path]
        .some((value) => value.toLocaleLowerCase().includes(normalizedQuery));
    const statusMatch = filter === "all" || (filter === "active" ? category.is_active : !category.is_active);
    return queryMatch && statusMatch;
  };
  return filterCategoryTree(nodes, matches);
}

function isNarrowViewport() {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    && window.matchMedia("(max-width: 1023px)").matches;
}

export function AdminCategoriesPage() {
  const [categories, setCategories] = useState<ManagedCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<CategoryFilter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [draft, setDraft] = useState<CategoryDraft | null>(null);
  const [draftCategoryId, setDraftCategoryId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createDraft, setCreateDraft] = useState({ parent_id: "", display_code: "", display_name: "", sort_order: "0" });
  const [createSaving, setCreateSaving] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [structureMode, setStructureMode] = useState(false);
  const [moving, setMoving] = useState(false);
  const [moveError, setMoveError] = useState<string | null>(null);
  const [moveOpen, setMoveOpen] = useState(false);
  const [moveTargetParentId, setMoveTargetParentId] = useState("");
  const [pendingMove, setPendingMove] = useState<CategoryMove | null>(null);
  const nodeRefs = useRef(new Map<string, HTMLDivElement>());
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const selectedCategory = categories.find((category) => category.id === selectedId) || null;
  const isDirty = Boolean(selectedCategory && draft && draftCategoryId === selectedCategory.id
    && (draft.display_code !== selectedCategory.display_code
      || draft.display_name !== selectedCategory.display_name
      || draft.sort_order !== selectedCategory.sort_order
      || draft.is_active !== selectedCategory.is_active));

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      const rows = await adminContentApi.categories(true);
      setCategories(rows);
      setSelectedId((current) => current && rows.some((category) => category.id === current) ? current : rows[0]?.id || null);
      return rows;
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "分类加载失败");
      return undefined;
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    if (!selectedCategory) {
      setDraft(null);
      setDraftCategoryId(null);
      return;
    }
    if (draftCategoryId !== selectedCategory.id || !isDirty) {
      setDraft(makeDraft(selectedCategory));
      setDraftCategoryId(selectedCategory.id);
      setSaveError(null);
    }
  }, [draftCategoryId, isDirty, selectedCategory]);

  const tree = useMemo(() => filterTree(buildCategoryTree(categories), query, filter), [categories, filter, query]);
  const visibleNodes = useMemo(() => flattenVisibleCategoryTree(tree, expanded), [expanded, tree]);
  const filteredCount = useMemo(() => countCategoryTreeNodes(tree), [tree]);
  const hasNestedNodes = categories.some((category) => categories.some((child) => child.parent_id === category.id));
  const allExpanded = hasNestedNodes && categories.filter((category) => categories.some((child) => child.parent_id === category.id)).every((category) => expanded.has(category.id));
  const activeChildIds = useMemo(() => {
    const result = new Set<string>();
    categories.filter((category) => category.is_active && category.parent_id).forEach((category) => result.add(category.parent_id as string));
    return result;
  }, [categories]);
  const selectedSiblings = useMemo(
    () => selectedCategory
      ? categories.filter((category) => category.parent_id === selectedCategory.parent_id).sort(compareManagedCategories)
      : [],
    [categories, selectedCategory],
  );
  const movingCategory = useMemo(
    () => pendingMove ? categories.find((category) => category.id === pendingMove.categoryId) || null : selectedCategory,
    [categories, pendingMove, selectedCategory],
  );
  const moveParentOptions = useMemo(() => {
    if (!movingCategory) return [];
    const descendants = new Set<string>();
    const visit = (parentId: string) => categories.filter((category) => category.parent_id === parentId).forEach((child) => {
      descendants.add(child.id);
      visit(child.id);
    });
    visit(movingCategory.id);
    const subtreeDepth = Math.max(0, ...categories.filter((category) => descendants.has(category.id)).map((category) => category.level - movingCategory.level));
    return categories.filter((category) => category.id !== movingCategory.id
      && !descendants.has(category.id)
      && category.is_active
      && category.level + 1 + subtreeDepth <= 4);
  }, [categories, movingCategory]);

  useEffect(() => {
    if (!query.trim() && filter === "all") return;
    const matchingBranches = collectExpandableCategoryIds(tree);
    setExpanded((current) => {
      const next = new Set(current);
      matchingBranches.forEach((id) => next.add(id));
      return next.size === current.size ? current : next;
    });
  }, [filter, query, tree]);

  const applySelection = useCallback((id: string) => {
    const category = categories.find((item) => item.id === id);
    if (!category) return;
    setSelectedId(id);
    setExpanded((current) => new Set([...current, ...collectCategoryAncestorIds(categories, id)]));
    if (isNarrowViewport()) setEditorOpen(true);
  }, [categories]);

  const requestAction = useCallback((action: Exclude<PendingAction, null>) => {
    if (isDirty) {
      setPendingAction(action);
      setDiscardOpen(true);
      return false;
    }
    return true;
  }, [isDirty]);

  const selectCategory = (id: string) => {
    if (id === selectedId && isNarrowViewport()) {
      setEditorOpen(true);
      return;
    }
    if (requestAction({ kind: "select", id })) applySelection(id);
  };

  const requestRefresh = () => {
    if (requestAction({ kind: "refresh" })) void load(true);
  };

  const requestCreate = (parentId: string | null = null) => {
    if (!requestAction({ kind: "create", parentId })) return;
    const siblings = categories.filter((category) => category.parent_id === parentId);
    const nextSort = siblings.length ? Math.max(...siblings.map((category) => category.sort_order)) + 10 : 10;
    setCreateDraft({ parent_id: parentId || "", display_code: "", display_name: "", sort_order: String(nextSort) });
    setCreateError(null);
    setCreateOpen(true);
  };

  const confirmDiscard = () => {
    setDiscardOpen(false);
    setPendingAction(null);
    if (selectedCategory) setDraft(makeDraft(selectedCategory));
    setDraftCategoryId(selectedCategory?.id || null);
    setSaveError(null);
    const action = pendingAction;
    if (!action) return;
    if (action.kind === "select") applySelection(action.id);
    if (action.kind === "refresh") void load(true);
    if (action.kind === "close") setEditorOpen(false);
    if (action.kind === "create") {
      const siblings = categories.filter((category) => category.parent_id === action.parentId);
      const nextSort = siblings.length ? Math.max(...siblings.map((category) => category.sort_order)) + 10 : 10;
      setCreateDraft({ parent_id: action.parentId || "", display_code: "", display_name: "", sort_order: String(nextSort) });
      setCreateError(null);
      setCreateOpen(true);
    }
  };

  const saveSelected = async () => {
    if (!selectedCategory || !draft) return;
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await adminContentApi.updateCategory(selectedCategory.id, {
        display_code: draft.display_code.trim(),
        display_name: draft.display_name.trim(),
        sort_order: Number(draft.sort_order) || 0,
        is_active: draft.is_active,
        expected_version: selectedCategory.version,
      });
      setDraft(makeDraft(updated));
      await load(true);
      toast.success(`${updated.display_name}已保存`);
    } catch (saveErrorValue) {
      setSaveError(saveErrorValue instanceof Error ? saveErrorValue.message : "分类保存失败");
    } finally {
      setSaving(false);
    }
  };

  const create = async () => {
    setCreateSaving(true);
    setCreateError(null);
    try {
      const created = await adminContentApi.createCategory({
        parent_id: createDraft.parent_id || null,
        display_code: createDraft.display_code.trim(),
        display_name: createDraft.display_name.trim(),
        sort_order: Number(createDraft.sort_order) || 0,
      });
      const rows = await load(true);
      setCreateOpen(false);
      if (rows) {
        setSelectedId(created.id);
        setDraft(makeDraft(created));
        setDraftCategoryId(created.id);
        setExpanded((current) => new Set([...current, ...collectCategoryAncestorIds(rows, created.id)]));
      }
      toast.success("分类已创建");
    } catch (createErrorValue) {
      setCreateError(createErrorValue instanceof Error ? createErrorValue.message : "分类创建失败");
    } finally {
      setCreateSaving(false);
    }
  };

  const cancelEdit = () => {
    if (selectedCategory) setDraft(makeDraft(selectedCategory));
    setDraftCategoryId(selectedCategory?.id || null);
    setSaveError(null);
  };

  const executeMove = async (move: CategoryMove) => {
    const category = categories.find((item) => item.id === move.categoryId);
    if (!category) return;
    setMoving(true);
    setMoveError(null);
    try {
      const rows = await adminContentApi.moveCategory(category.id, {
        target_parent_id: move.targetParentId,
        before_category_id: move.beforeCategoryId,
        expected_version: category.version,
      });
      setCategories(rows);
      setExpanded((current) => new Set([...current, ...collectCategoryAncestorIds(rows, category.id)]));
      setMoveOpen(false);
      setPendingMove(null);
      toast.success(`${category.display_name}已移动`);
    } catch (moveErrorValue) {
      setMoveError(moveErrorValue instanceof Error ? moveErrorValue.message : "分类移动失败");
    } finally {
      setMoving(false);
    }
  };

  const moveRelative = (offset: -1 | 1) => {
    if (!selectedCategory) return;
    const index = selectedSiblings.findIndex((category) => category.id === selectedCategory.id);
    const targetIndex = index + offset;
    if (index < 0 || targetIndex < 0 || targetIndex >= selectedSiblings.length) return;
    const beforeCategoryId = offset < 0
      ? selectedSiblings[targetIndex].id
      : selectedSiblings[targetIndex + 1]?.id || null;
    void executeMove({ categoryId: selectedCategory.id, targetParentId: selectedCategory.parent_id, beforeCategoryId });
  };

  const openMoveDialog = (move?: CategoryMove) => {
    const category = move ? categories.find((item) => item.id === move.categoryId) : selectedCategory;
    if (!category || isDirty) return;
    const targetParentId = move?.targetParentId ?? category.parent_id;
    setPendingMove(move || null);
    setMoveTargetParentId(targetParentId || "");
    setMoveError(null);
    setMoveOpen(true);
  };

  const confirmMove = () => {
    if (!movingCategory) return;
    const targetParentId = moveTargetParentId || null;
    void executeMove(pendingMove
      ? { ...pendingMove, targetParentId, beforeCategoryId: pendingMove.targetParentId === targetParentId ? pendingMove.beforeCategoryId : null }
      : { categoryId: movingCategory.id, targetParentId, beforeCategoryId: null });
  };

  const handleDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return;
    const dragged = categories.find((category) => category.id === active.id);
    const target = categories.find((category) => category.id === over.id);
    if (!dragged || !target) return;
    if (dragged.parent_id !== target.parent_id) {
      const targetParentId = target.is_active && target.level < 4 ? target.id : target.parent_id;
      openMoveDialog({ categoryId: dragged.id, targetParentId, beforeCategoryId: null });
      return;
    }
    const siblings = categories.filter((category) => category.parent_id === dragged.parent_id)
      .sort(compareManagedCategories);
    const activeIndex = siblings.findIndex((category) => category.id === dragged.id);
    const overIndex = siblings.findIndex((category) => category.id === target.id);
    const beforeCategoryId = activeIndex < overIndex ? siblings[overIndex + 1]?.id || null : target.id;
    void executeMove({ categoryId: dragged.id, targetParentId: dragged.parent_id, beforeCategoryId });
  };

  const handleEditorOpenChange = (open: boolean) => {
    if (open) {
      setEditorOpen(true);
      return;
    }
    if (requestAction({ kind: "close" })) setEditorOpen(false);
  };

  return (
    <section className="space-y-5" aria-labelledby="managed-categories-title">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-ui-xs font-medium text-primary">内容管理</p>
          <h1 id="managed-categories-title" className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">分类管理</h1>
          <p className="mt-1 text-ui-sm text-muted-foreground">维护资料分类、层级和可用状态。</p>
        </div>
        <div className="flex w-full gap-2 sm:w-auto">
          <IconButton label={refreshing ? "刷新中" : "刷新"} onClick={requestRefresh} disabled={loading || refreshing}>
            <RefreshCw className={refreshing ? "size-4 animate-spin" : "size-4"} />
          </IconButton>
          <Button onClick={() => requestCreate()} className="flex-1 sm:flex-none"><Plus className="size-4" />新增分类</Button>
        </div>
      </header>

      {error && categories.length > 0 && <Alert variant="destructive" role="alert"><AlertTitle>分类刷新失败</AlertTitle><AlertDescription>{error}，当前仍显示上一次结果。</AlertDescription></Alert>}
      {error && categories.length === 0 && <ErrorState title="分类管理加载失败" description={error} action={<Button variant="outline" size="sm" onClick={() => void load()}>重新加载</Button>} />}

      {loading ? <LoadingState className="min-h-48 border border-border" label="正在加载分类…" /> : error && categories.length === 0 ? null : categories.length === 0 ? (
        <EmptyState title="暂无分类" description="新增第一个分类后，可在此维护名称、编号和状态。" action={<Button onClick={() => requestCreate()}><Plus className="size-4" />新增分类</Button>} />
      ) : (
        <section aria-labelledby="category-list-title" className="overflow-hidden rounded-ui-lg border border-border bg-card">
          <div>
            <div className="flex flex-col gap-3 bg-surface-muted/30 px-4 py-3 sm:flex-row sm:items-end">
              <label className="min-w-0 flex-1 space-y-1 text-ui-xs font-medium text-muted-foreground sm:max-w-md">
                <span>搜索分类</span>
                <span className="relative block"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input type="search" className="bg-background pl-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="编号、名称或完整路径" aria-label="搜索分类" /></span>
              </label>
              <label className="space-y-1 text-ui-xs font-medium text-muted-foreground sm:w-40"><span>状态</span><Select className="bg-background" value={filter} onChange={(event) => setFilter(event.target.value as CategoryFilter)} aria-label="分类状态"><option value="all">全部状态</option><option value="active">仅启用</option><option value="inactive">仅停用</option></Select></label>
            </div>
            <div className="flex min-h-control-lg flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-2.5">
              <div className="flex min-w-0 items-center gap-3">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-ui-md bg-primary/10 text-primary"><FolderTree className="size-4" aria-hidden="true" /></span>
                <div className="min-w-0">
                  <h2 id="category-list-title" className="text-ui-sm font-semibold">分类目录</h2>
                  <p className="text-ui-xs tabular-nums text-muted-foreground">{tree.length} 个一级分类 · 共 {filteredCount} 个分类</p>
                </div>
              </div>
              <div className="flex flex-wrap items-center justify-end gap-1">
                {hasNestedNodes && <Button size="sm" variant="ghost" onClick={() => setExpanded(allExpanded ? new Set() : new Set(categories.filter((category) => categories.some((child) => child.parent_id === category.id)).map((category) => category.id)))}>
                  {allExpanded ? <ChevronsUp className="size-4" aria-hidden="true" /> : <ChevronsDown className="size-4" aria-hidden="true" />}
                  {allExpanded ? "全部折叠" : "全部展开"}
                </Button>}
                <Button size="sm" variant={structureMode ? "secondary" : "ghost"} aria-pressed={structureMode} disabled={isDirty || moving} title={isDirty ? "请先保存或取消当前修改" : "调整分类顺序和父级"} onClick={() => setStructureMode((current) => !current)}>
                  <Move className="size-4" aria-hidden="true" />{structureMode ? "完成调整" : "调整结构"}
                </Button>
              </div>
            </div>
          </div>

          {tree.length === 0 ? <EmptyState className="rounded-none border-0 border-t border-border bg-card" title="没有符合条件的分类" description="请调整搜索词或状态筛选。" /> : (
            <div className="grid min-h-[28rem] border-t border-border lg:h-[calc(100vh-20rem)] lg:min-h-[22rem] lg:max-h-[40rem] lg:grid-cols-[minmax(22rem,0.88fr)_minmax(25rem,1.12fr)]">
              <div className="min-h-0 min-w-0 border-border bg-background/30 lg:overflow-y-auto lg:border-r">
                {structureMode && <div className="border-b border-border bg-primary/5 px-4 py-2 text-ui-xs text-muted-foreground" role="status">拖动手柄调整同级顺序；跨层级移动会要求确认。</div>}
                {moveError && <Alert variant="destructive" className="m-3" role="alert"><AlertTitle>结构调整失败</AlertTitle><AlertDescription>{moveError}</AlertDescription></Alert>}
                <DndContext sensors={sensors} collisionDetection={closestCenter} onDragStart={({ active }) => setSelectedId(String(active.id))} onDragEnd={handleDragEnd}>
                  <SortableContext items={visibleNodes.map((node) => node.category.id)} strategy={verticalListSortingStrategy}>
                    <div role="tree" aria-label="分类层级" aria-busy={moving} className="border-b border-border">
                      {tree.map((node, index) => <CategoryTreeNodeView key={node.category.id} node={node} level={1} index={index} siblingCount={tree.length} selectedId={selectedId} expanded={expanded} visibleNodes={visibleNodes} nodeRefs={nodeRefs} structureMode={structureMode} moving={moving} onSelect={selectCategory} onToggle={(id) => setExpanded((current) => { const next = new Set(current); next.has(id) ? next.delete(id) : next.add(id); return next; })} />)}
                    </div>
                  </SortableContext>
                </DndContext>
              </div>
              <div className="hidden min-h-0 min-w-0 overflow-hidden lg:block">
                <CategoryDetail category={selectedCategory} draft={draft} categories={categories} saving={saving} moving={moving} error={saveError} hasActiveChild={selectedCategory ? activeChildIds.has(selectedCategory.id) : false} onChange={setDraft} onSave={() => void saveSelected()} onCancel={cancelEdit} onAddChild={() => selectedCategory && requestCreate(selectedCategory.id)} onMove={() => openMoveDialog()} onMoveUp={() => moveRelative(-1)} onMoveDown={() => moveRelative(1)} canMoveUp={Boolean(selectedCategory && selectedSiblings[0]?.id !== selectedCategory.id)} canMoveDown={Boolean(selectedCategory && selectedSiblings.at(-1)?.id !== selectedCategory.id)} />
              </div>
            </div>
          )}
        </section>
      )}

      <Sheet open={createOpen} onOpenChange={(open) => { if (!open && !createSaving) setCreateOpen(false); }}>
        <SheetContent className="max-w-xl overflow-y-auto">
          <SheetHeader><SheetTitle>新增分类</SheetTitle><SheetDescription>分类最多四级，稳定标识由系统自动生成。</SheetDescription></SheetHeader>
          <div className="space-y-5 p-6">
            {createError && <Alert variant="destructive" role="alert"><AlertTitle>分类创建失败</AlertTitle><AlertDescription>{createError}</AlertDescription></Alert>}
            <CategoryCreateForm draft={createDraft} categories={categories} saving={createSaving} onChange={setCreateDraft} onCreate={() => void create()} />
          </div>
        </SheetContent>
      </Sheet>

      <Sheet open={editorOpen} onOpenChange={handleEditorOpenChange}>
        <SheetContent className="max-w-xl overflow-y-auto lg:hidden">
          <SheetHeader><SheetTitle>{selectedCategory?.display_name || "编辑分类"}</SheetTitle><SheetDescription>{selectedCategory?.full_path || "维护分类信息"}</SheetDescription></SheetHeader>
          <div className="p-6"><CategoryDetail category={selectedCategory} draft={draft} categories={categories} saving={saving} moving={moving} error={saveError} hasActiveChild={selectedCategory ? activeChildIds.has(selectedCategory.id) : false} onChange={setDraft} onSave={() => void saveSelected()} onCancel={cancelEdit} onAddChild={() => selectedCategory && requestCreate(selectedCategory.id)} onMove={() => openMoveDialog()} onMoveUp={() => moveRelative(-1)} onMoveDown={() => moveRelative(1)} canMoveUp={Boolean(selectedCategory && selectedSiblings[0]?.id !== selectedCategory.id)} canMoveDown={Boolean(selectedCategory && selectedSiblings.at(-1)?.id !== selectedCategory.id)} /></div>
        </SheetContent>
      </Sheet>

      <Dialog open={moveOpen} onOpenChange={(open) => { if (!open && !moving) { setMoveOpen(false); setPendingMove(null); setMoveError(null); } }}>
        <DialogContent>
          <DialogHeader><DialogTitle>移动分类</DialogTitle><DialogDescription>移动会改变分类路径，但不会改变分类标识或资料归属。</DialogDescription></DialogHeader>
          <div className="space-y-4">
            {moveError && <Alert variant="destructive" role="alert"><AlertTitle>移动失败</AlertTitle><AlertDescription>{moveError}</AlertDescription></Alert>}
            <div className="rounded-ui-md border border-border bg-surface-muted/40 px-3 py-2 text-ui-sm"><p className="text-ui-xs text-muted-foreground">当前路径</p><p className="mt-1 break-words font-medium">{movingCategory?.full_path}</p></div>
            <Field label="目标父分类"><Select value={moveTargetParentId} onChange={(event) => setMoveTargetParentId(event.target.value)} aria-label="目标父分类"><option value="">一级分类</option>{moveParentOptions.map((category) => <option key={category.id} value={category.id}>{category.full_path}</option>)}</Select></Field>
            <p className="text-ui-xs text-muted-foreground">新路径：{moveTargetParentId ? `${categories.find((category) => category.id === moveTargetParentId)?.full_path} / ` : ""}{movingCategory?.display_code} {movingCategory?.display_name}</p>
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setMoveOpen(false)} disabled={moving}>取消</Button><Button onClick={confirmMove} disabled={moving || (!pendingMove && movingCategory?.parent_id === (moveTargetParentId || null))}><Move className="size-4" />{moving ? "移动中…" : "确认移动"}</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={discardOpen} onOpenChange={(open) => { if (!open) { setDiscardOpen(false); setPendingAction(null); } }}>
        <DialogContent>
          <DialogHeader><DialogTitle>放弃未保存修改？</DialogTitle><DialogDescription>当前分类有尚未保存的修改，继续操作会丢弃这些内容。</DialogDescription></DialogHeader>
          <DialogFooter><Button variant="outline" onClick={() => { setDiscardOpen(false); setPendingAction(null); }}>继续编辑</Button><Button variant="destructive" onClick={confirmDiscard}>放弃修改</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

function CategoryTreeNodeView({
  node,
  level,
  index,
  siblingCount,
  selectedId,
  expanded,
  visibleNodes,
  nodeRefs,
  structureMode,
  moving,
  onSelect,
  onToggle,
}: {
  node: CategoryTreeNode;
  level: number;
  index: number;
  siblingCount: number;
  selectedId: string | null;
  expanded: Set<string>;
  visibleNodes: CategoryTreeNode[];
  nodeRefs: MutableRefObject<Map<string, HTMLDivElement>>;
  structureMode: boolean;
  moving: boolean;
  onSelect: (id: string) => void;
  onToggle: (id: string) => void;
}) {
  const { category, children } = node;
  const isExpanded = expanded.has(category.id);
  const hasChildren = children.length > 0;
  const visibleIndex = visibleNodes.findIndex((item) => item.category.id === category.id);
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: category.id, disabled: !structureMode || moving });
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const currentIndex = visibleIndex < 0 ? index : visibleIndex;
    const previous = visibleNodes[Math.max(0, currentIndex - 1)]?.category.id;
    const next = visibleNodes[Math.min(visibleNodes.length - 1, currentIndex + 1)]?.category.id;
    if (event.key === "ArrowDown" && next) { event.preventDefault(); onSelect(next); nodeRefs.current.get(next)?.focus(); }
    if (event.key === "ArrowUp" && previous) { event.preventDefault(); onSelect(previous); nodeRefs.current.get(previous)?.focus(); }
    if (event.key === "Home" && visibleNodes[0]) { event.preventDefault(); onSelect(visibleNodes[0].category.id); nodeRefs.current.get(visibleNodes[0].category.id)?.focus(); }
    if (event.key === "End" && visibleNodes.at(-1)) { event.preventDefault(); onSelect(visibleNodes.at(-1)!.category.id); nodeRefs.current.get(visibleNodes.at(-1)!.category.id)?.focus(); }
    if (event.key === "ArrowRight" && hasChildren) {
      event.preventDefault();
      if (!isExpanded) onToggle(category.id);
      else {
        const childId = children[0].category.id;
        onSelect(childId);
        nodeRefs.current.get(childId)?.focus();
      }
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (isExpanded) onToggle(category.id);
      else if (category.parent_id) {
        onSelect(category.parent_id);
        nodeRefs.current.get(category.parent_id)?.focus();
      }
    }
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(category.id); }
  };

  return <>
    <div
      ref={(element) => { setNodeRef(element); if (element) nodeRefs.current.set(category.id, element); else nodeRefs.current.delete(category.id); }}
      role="treeitem"
      aria-level={level}
      aria-setsize={siblingCount}
      aria-posinset={index + 1}
      aria-expanded={hasChildren ? isExpanded : undefined}
      aria-selected={selectedId === category.id}
      tabIndex={selectedId === category.id ? 0 : -1}
      data-testid={`category-tree-item-${category.id}`}
      style={{ transform: CSS.Transform.toString(transform), transition, zIndex: isDragging ? 10 : undefined }}
      onClick={() => onSelect(category.id)}
      onKeyDown={onKeyDown}
      className={`relative flex min-h-[3.25rem] cursor-pointer items-center gap-2 border-b border-l-2 border-b-border py-2 pr-3 outline-none transition-colors duration-normal focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${level === 1 ? "pl-3" : "pl-3 before:absolute before:left-0 before:top-1/2 before:w-3 before:border-t before:border-border"} ${selectedId === category.id ? "border-l-primary bg-primary/10" : "border-l-transparent hover:bg-surface-muted/60"} ${isDragging ? "bg-card opacity-80 shadow-ui-md" : ""}`}
    >
      {structureMode && <button type="button" aria-label={`拖动${category.display_name}`} title="拖动调整顺序" onClick={(event) => event.stopPropagation()} className="hidden size-7 shrink-0 cursor-grab items-center justify-center rounded-ui-sm text-muted-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring active:cursor-grabbing lg:inline-flex" {...attributes} {...listeners}><GripVertical className="size-4" /></button>}
      <span className="flex w-11 shrink-0 items-center gap-1">
        {hasChildren ? <button type="button" aria-label={isExpanded ? `收起${category.display_name}` : `展开${category.display_name}`} title={isExpanded ? "收起" : "展开"} onClick={(event) => { event.stopPropagation(); onToggle(category.id); }} className="inline-flex size-6 items-center justify-center rounded-ui-sm text-muted-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{isExpanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}</button> : <span className="size-6" aria-hidden="true" />}
        {hasChildren && isExpanded ? <FolderOpen className="size-4 text-primary/80" aria-hidden="true" /> : <Folder className="size-4 text-muted-foreground" aria-hidden="true" />}
      </span>
      <span className="flex min-w-0 flex-1 items-center gap-2">
        <span className={`inline-flex w-11 shrink-0 items-center gap-1 text-ui-xs font-medium ${category.is_active ? "text-success" : "text-muted-foreground"}`}><span className={`size-2 rounded-full ${category.is_active ? "bg-success" : "bg-muted-foreground/60"}`} aria-hidden="true" />{category.is_active ? "启用" : "停用"}</span>
        <span className={`min-w-0 flex-1 break-words tabular-nums ${level === 1 ? "font-semibold" : "font-medium"}`}>{category.display_code} {category.display_name}</span>
        <span className="hidden shrink-0 text-right text-ui-xs tabular-nums text-muted-foreground sm:block">{category.item_count} 份{hasChildren ? ` · ${children.length} 项` : ""}</span>
      </span>
    </div>
    {hasChildren && isExpanded && <div role="group" className="ml-5 border-l border-border bg-surface-muted/10 sm:ml-6">{children.map((child, childIndex) => <CategoryTreeNodeView key={child.category.id} node={child} level={level + 1} index={childIndex} siblingCount={children.length} selectedId={selectedId} expanded={expanded} visibleNodes={visibleNodes} nodeRefs={nodeRefs} structureMode={structureMode} moving={moving} onSelect={onSelect} onToggle={onToggle} />)}</div>}
  </>;
}

function CategoryDetail({
  category,
  draft,
  categories,
  saving,
  moving,
  error,
  hasActiveChild,
  onChange,
  onSave,
  onCancel,
  onAddChild,
  onMove,
  onMoveUp,
  onMoveDown,
  canMoveUp,
  canMoveDown,
}: {
  category: ManagedCategory | null;
  draft: CategoryDraft | null;
  categories: ManagedCategory[];
  saving: boolean;
  moving: boolean;
  error: string | null;
  hasActiveChild: boolean;
  onChange: (draft: CategoryDraft | null) => void;
  onSave: () => void;
  onCancel: () => void;
  onAddChild: () => void;
  onMove: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  canMoveUp: boolean;
  canMoveDown: boolean;
}) {
  if (!category || !draft) return <EmptyState title="选择一个分类" description="从左侧选择分类后，在此维护分类信息。" />;
  const isDirty = draft.display_code !== category.display_code || draft.display_name !== category.display_name || draft.sort_order !== category.sort_order || draft.is_active !== category.is_active;
  const cannotDisable = draft.is_active && (category.item_count > 0 || hasActiveChild);
  const statusHelpId = `category-status-help-${category.id}`;
  const parent = categories.find((item) => item.id === category.parent_id);
  return <div className="flex h-full flex-col">
    <div className="border-b border-border px-5 py-4 sm:px-6"><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold">{category.display_code} {category.display_name}</h3><Badge variant={category.is_active ? "success" : "secondary"}>{category.is_active ? "启用" : "停用"}</Badge>{isDirty && draft.is_active !== category.is_active && <Badge variant="warning">待保存：{draft.is_active ? "启用" : "停用"}</Badge>}</div><p className="mt-1 break-words text-ui-xs text-muted-foreground">{category.full_path}</p></div>
    <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
      {error && <Alert variant="destructive" role="alert"><AlertTitle>保存失败</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
      <section aria-labelledby={`category-fields-${category.id}`} className="space-y-4">
        <h4 id={`category-fields-${category.id}`} className="text-ui-sm font-semibold">基本信息</h4>
        <div className="grid gap-4 sm:grid-cols-2"><Field label="显示编号"><Input value={draft.display_code} onChange={(event) => onChange({ ...draft, display_code: event.target.value })} aria-label="显示编号" /></Field><Field label="显示名称"><Input value={draft.display_name} onChange={(event) => onChange({ ...draft, display_name: event.target.value })} aria-label="显示名称" /></Field></div>
        <Field label="排序序号"><Input type="number" min={0} max={999999} value={draft.sort_order} onChange={(event) => onChange({ ...draft, sort_order: Number(event.target.value) || 0 })} aria-label="排序序号" /><span className="mt-1 block text-ui-xs font-normal text-muted-foreground">同级允许使用相同序号；相同时按名称排序，0 表示未设置并排在末尾。</span></Field>
      </section>
      <section aria-labelledby={`category-status-${category.id}`} className="space-y-3 border-t border-border pt-4">
        <h4 id={`category-status-${category.id}`} className="text-ui-sm font-semibold">可用状态</h4>
        <div role="radiogroup" aria-labelledby={`category-status-${category.id}`} aria-describedby={cannotDisable ? statusHelpId : undefined} className="grid gap-2 sm:grid-cols-2">
          <label className={`flex min-h-control-md items-start gap-2 rounded-ui-md border px-3 py-2 text-ui-sm transition-colors focus-within:ring-2 focus-within:ring-ring ${draft.is_active ? "border-primary/50 bg-primary/10" : "border-border hover:bg-surface-muted/60"}`}>
            <input type="radio" name={`category-status-${category.id}`} value="active" checked={draft.is_active} disabled={saving} onChange={() => onChange({ ...draft, is_active: true })} className="peer sr-only" aria-label={`${category.display_name}启用`} />
            <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border border-input peer-checked:border-primary"><span className="size-2 rounded-full bg-primary opacity-0 peer-checked:opacity-100" /></span>
            <span><span className="block font-medium">启用</span><span className="block text-ui-xs text-muted-foreground">可用于上传和归类</span></span>
          </label>
          <label className={`flex min-h-control-md items-start gap-2 rounded-ui-md border px-3 py-2 text-ui-sm transition-colors focus-within:ring-2 focus-within:ring-ring ${cannotDisable ? "cursor-not-allowed border-border bg-surface-muted/40 opacity-60" : draft.is_active ? "border-border hover:bg-surface-muted/60" : "border-primary/50 bg-primary/10"}`}>
            <input type="radio" name={`category-status-${category.id}`} value="inactive" checked={!draft.is_active} disabled={saving || cannotDisable} onChange={() => onChange({ ...draft, is_active: false })} className="peer sr-only" aria-label={`${category.display_name}停用`} />
            <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border border-input peer-checked:border-primary"><span className="size-2 rounded-full bg-primary opacity-0 peer-checked:opacity-100" /></span>
            <span><span className="block font-medium">停用</span><span className="block text-ui-xs text-muted-foreground">不再出现在可选目录中</span></span>
          </label>
        </div>
        {cannotDisable && <div id={statusHelpId} className="flex gap-2 rounded-ui-md border border-border bg-surface-muted/50 px-3 py-2 text-ui-xs text-muted-foreground" role="status"><TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" /><div className="space-y-1"><p className="font-medium text-foreground">暂不能停用</p>{category.item_count > 0 && <p>该分类有 {category.item_count} 份直接资料，需重新归类后才能停用。</p>}{hasActiveChild && <p>该分类仍有启用的子分类，请先停用子分类。</p>}</div></div>}
      </section>
      <section aria-labelledby={`category-level-${category.id}`} className="space-y-3 border-t border-border pt-4"><div><h4 id={`category-level-${category.id}`} className="text-ui-sm font-semibold">目录结构</h4><p className="mt-1 break-words text-ui-xs text-muted-foreground">父分类：{parent ? `${parent.display_code} ${parent.display_name}` : "一级分类"} · 第 {category.level} 级 · {category.item_count} 份直接资料</p></div><div className="flex flex-wrap gap-2"><Button variant="outline" onClick={onAddChild} disabled={moving || category.level >= 4 || !category.is_active}><Plus className="size-4" />新增子分类</Button><Button variant="outline" onClick={onMove} disabled={moving || isDirty}><Move className="size-4" />移动至</Button><IconButton label="上移分类" onClick={onMoveUp} disabled={moving || isDirty || !canMoveUp}><ArrowUp className="size-4" /></IconButton><IconButton label="下移分类" onClick={onMoveDown} disabled={moving || isDirty || !canMoveDown}><ArrowDown className="size-4" /></IconButton></div></section>
    </div>
    <div className="flex flex-col-reverse gap-2 border-t border-border px-5 py-4 sm:flex-row sm:justify-end"><Button variant="outline" onClick={onCancel} disabled={saving || moving || !isDirty}>取消</Button><Button onClick={onSave} disabled={saving || moving || !isDirty || !draft.display_code.trim() || !draft.display_name.trim()}><Save className="size-4" />{saving ? "保存中…" : "保存修改"}</Button></div>
  </div>;
}

function CategoryCreateForm({
  draft,
  categories,
  saving,
  onChange,
  onCreate,
}: {
  draft: { parent_id: string; display_code: string; display_name: string; sort_order: string };
  categories: ManagedCategory[];
  saving: boolean;
  onChange: (draft: { parent_id: string; display_code: string; display_name: string; sort_order: string }) => void;
  onCreate: () => void;
}) {
  const canCreate = draft.display_code.trim() && draft.display_name.trim();
  return <div className="space-y-4"><Field label="父分类"><Select value={draft.parent_id} onChange={(event) => onChange({ ...draft, parent_id: event.target.value })} aria-label="父分类"><option value="">一级分类</option>{categories.filter((category) => category.is_active && category.level < 4).map((category) => <option key={category.id} value={category.id}>{category.full_path}</option>)}</Select></Field><div className="grid gap-4 sm:grid-cols-2"><Field label="显示编号"><Input value={draft.display_code} onChange={(event) => onChange({ ...draft, display_code: event.target.value })} placeholder="例如 03" aria-label="显示编号" /></Field><Field label="分类名称"><Input value={draft.display_name} onChange={(event) => onChange({ ...draft, display_name: event.target.value })} placeholder="例如 公司内部标准" aria-label="分类名称" /></Field></div><Field label="排序序号"><Input type="number" min={0} max={999999} value={draft.sort_order} onChange={(event) => onChange({ ...draft, sort_order: event.target.value })} aria-label="排序序号" /><span className="mt-1 block text-ui-xs font-normal text-muted-foreground">建议使用 10、20、30 等间隔值；0 表示未设置。</span></Field><Button className="w-full" onClick={onCreate} disabled={saving || !canCreate}><Plus className="size-4" />{saving ? "新增中…" : "新增分类"}</Button></div>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="space-y-1.5 text-ui-sm font-medium"><span>{label}</span>{children}</label>;
}
