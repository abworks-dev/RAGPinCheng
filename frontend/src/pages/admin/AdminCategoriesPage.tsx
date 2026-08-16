import { useCallback, useEffect, useState } from "react";
import { Plus, RefreshCw, Save } from "lucide-react";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Checkbox } from "../../components/ui/checkbox";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import { Select } from "../../components/ui/select";
import { toast } from "../../components/ui/toast";
import type { ManagedCategory } from "../../types";

export function AdminCategoriesPage() {
  const [categories, setCategories] = useState<ManagedCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ parent_id: "", display_code: "", display_name: "", sort_order: "0" });

  const load = useCallback(async (refresh = false) => {
    refresh ? setRefreshing(true) : setLoading(true);
    setError(null);
    try {
      const categoryRows = await api.managedCategories(true);
      setCategories(categoryRows);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "分类加载失败");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const create = async () => {
    setSaving(true);
    try {
      await api.createManagedCategory({
        parent_id: form.parent_id || null,
        display_code: form.display_code.trim(),
        display_name: form.display_name.trim(),
        sort_order: Number(form.sort_order) || 0,
      });
      setForm({ parent_id: "", display_code: "", display_name: "", sort_order: "0" });
      await load(true);
      toast.success("分类已创建");
    } catch (createError) {
      toast.error(createError instanceof Error ? createError.message : "分类创建失败");
    } finally {
      setSaving(false);
    }
  };

  return <section className="flex flex-col gap-6" aria-labelledby="managed-categories-title">
    <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <p className="text-ui-xs font-medium text-primary">内容管理</p>
        <h1 id="managed-categories-title" className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">分类管理</h1>
        <p className="mt-1 text-ui-sm text-muted-foreground">维护资料分类、层级和可用状态。</p>
      </div>
      <Button variant="outline" size="sm" className="w-full sm:w-auto" onClick={() => void load(true)} disabled={loading || refreshing}>
        <RefreshCw className={refreshing ? "size-4 animate-spin" : "size-4"} />{refreshing ? "刷新中…" : "刷新"}
      </Button>
    </header>

    {error && <ErrorState title="分类管理加载失败" description={error} action={<Button variant="outline" size="sm" onClick={() => void load()}>重新加载</Button>} />}

    <section className="order-2 space-y-4 border-y border-border py-5 lg:order-1" aria-labelledby="new-category-title">
      <div><h2 id="new-category-title" className="text-ui-base font-semibold">新增分类</h2><p className="mt-1 text-ui-xs text-muted-foreground">分类最多四级；稳定标识由系统自动生成。</p></div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4 xl:items-end">
        <Field label="显示编号"><Input value={form.display_code} onChange={(event) => setForm({ ...form, display_code: event.target.value })} placeholder="例如 03" /></Field>
        <Field label="分类名称"><Input value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} placeholder="例如 公司内部标准" /></Field>
        <Field label="父分类"><Select value={form.parent_id} onChange={(event) => setForm({ ...form, parent_id: event.target.value })}><option value="">一级分类</option>{categories.filter((item) => item.is_active && item.level < 4).map((item) => <option key={item.id} value={item.id}>{item.display_code} {item.display_name}</option>)}</Select></Field>
        <Button className="w-full" onClick={() => void create()} disabled={saving || !form.display_code.trim() || !form.display_name.trim()}><Plus className="size-4" />{saving ? "新增中…" : "新增分类"}</Button>
      </div>
    </section>

    <section className="order-1 space-y-3 lg:order-2" aria-labelledby="category-list-title">
      <div className="flex items-center justify-between gap-3"><h2 id="category-list-title" className="text-ui-base font-semibold">现有分类</h2>{!loading && !error && <span className="text-ui-xs text-muted-foreground">共 {categories.length} 个</span>}</div>
      {loading ? <LoadingState className="min-h-48 border border-border" label="正在加载分类…" /> : !error && categories.length === 0 ? <EmptyState title="暂无分类" description="新增第一个分类后，可在此维护名称、编号和状态。" /> : !error && <div className="divide-y divide-border border-y border-border">{categories.map((category) => <CategoryEditor key={category.id} category={category} onSaved={() => load(true)} />)}</div>}
    </section>

  </section>;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return <label className="space-y-1.5 text-ui-sm font-medium"><span>{label}{hint && <span className="ml-1 font-normal text-muted-foreground">· {hint}</span>}</span>{children}</label>;
}

function CategoryEditor({ category, onSaved }: { category: ManagedCategory; onSaved: () => Promise<void> }) {
  const [code, setCode] = useState(category.display_code);
  const [name, setName] = useState(category.display_name);
  const [sortOrder, setSortOrder] = useState(String(category.sort_order));
  const [active, setActive] = useState(category.is_active);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await api.updateManagedCategory(category.id, { display_code: code.trim(), display_name: name.trim(), sort_order: Number(sortOrder) || 0, is_active: active, expected_version: category.version });
      await onSaved();
      toast.success(`${name.trim()}已保存`);
    } catch (saveError) {
      toast.error(saveError instanceof Error ? saveError.message : "分类保存失败");
    } finally { setSaving(false); }
  };

  return <article className="space-y-4 py-4">
    <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0" style={{ paddingLeft: `${Math.max(0, category.level - 1) * 16}px` }}><div className="flex flex-wrap items-center gap-2"><h3 className="font-medium">{category.display_code} {category.display_name}</h3><Badge variant={active ? "success" : "secondary"}>{active ? "启用" : "停用"}</Badge><Badge variant="outline">第 {category.level} 级</Badge><Badge variant="secondary">{category.item_count} 份资料</Badge></div><p className="mt-1 break-words text-ui-xs text-muted-foreground">{category.full_path || `${category.display_code} ${category.display_name}`}</p></div>
      <Button size="sm" variant="outline" className="w-full sm:w-auto" onClick={() => void save()} disabled={saving || !code.trim() || !name.trim()}><Save className="size-4" />{saving ? "保存中…" : "保存"}</Button>
    </div>
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[10rem_minmax(12rem,1fr)_8rem_auto] lg:items-end">
      <Field label="编号"><Input aria-label={`编辑${category.display_name}的编号`} value={code} onChange={(event) => setCode(event.target.value)} /></Field>
      <Field label="显示名称"><Input aria-label={`编辑${category.display_name}的显示名称`} value={name} onChange={(event) => setName(event.target.value)} /></Field>
      <Field label="排序"><Input aria-label={`编辑${category.display_name}的排序`} type="number" value={sortOrder} onChange={(event) => setSortOrder(event.target.value)} /></Field>
      <label className="flex h-control-md cursor-pointer items-center gap-2 text-ui-sm font-medium"><Checkbox aria-label={`${category.display_name}启用`} checked={active} disabled={category.item_count > 0 && active} onChange={(event) => setActive(event.target.checked)} />启用分类</label>
    </div>
  </article>;
}
