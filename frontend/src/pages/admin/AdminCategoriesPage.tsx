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
import { useAuth } from "../../context/AuthContext";
import type { ContentPermission, ContentPermissionUser, ManagedCategory } from "../../types";

export function AdminCategoriesPage() {
  const { state } = useAuth();
  const isAdmin = state.status === "authed" && state.user.role === "admin";
  const [categories, setCategories] = useState<ManagedCategory[]>([]);
  const [permissionUsers, setPermissionUsers] = useState<ContentPermissionUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ category_key: "", parent_id: "", display_code: "", display_name: "", sort_order: "0" });

  const load = useCallback(async (refresh = false) => {
    refresh ? setRefreshing(true) : setLoading(true);
    setError(null);
    try {
      const [categoryRows, permissionRows] = await Promise.all([
        api.managedCategories(true),
        isAdmin ? api.managedContentPermissions() : Promise.resolve([]),
      ]);
      setCategories(categoryRows);
      setPermissionUsers(permissionRows);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "分类加载失败");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [isAdmin]);

  useEffect(() => { void load(); }, [load]);

  const create = async () => {
    setSaving(true);
    try {
      await api.createManagedCategory({
        category_key: form.category_key.trim(),
        parent_id: form.parent_id || null,
        display_code: form.display_code.trim(),
        display_name: form.display_name.trim(),
        sort_order: Number(form.sort_order) || 0,
      });
      setForm({ category_key: "", parent_id: "", display_code: "", display_name: "", sort_order: "0" });
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
        <p className="text-ui-xs text-muted-foreground">资料管理</p>
        <h1 id="managed-categories-title" className="mt-1 text-ui-2xl font-semibold text-foreground">分类设置</h1>
        <p className="mt-1 text-ui-sm text-muted-foreground">维护资料分类及工作流权限。</p>
      </div>
      <Button variant="outline" size="sm" className="w-full sm:w-auto" onClick={() => void load(true)} disabled={loading || refreshing}>
        <RefreshCw className={refreshing ? "size-4 animate-spin" : "size-4"} />{refreshing ? "刷新中…" : "刷新"}
      </Button>
    </header>

    {error && <ErrorState title="分类设置加载失败" description={error} action={<Button variant="outline" size="sm" onClick={() => void load()}>重新加载</Button>} />}

    <section className="order-2 space-y-4 border-y border-border py-5 lg:order-1" aria-labelledby="new-category-title">
      <div><h2 id="new-category-title" className="text-ui-base font-semibold">新增分类</h2><p className="mt-1 text-ui-xs text-muted-foreground">稳定标识创建后用于系统关联，建议使用简短英文和下划线，日常管理主要使用编号和名称。</p></div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5 xl:items-end">
        <Field label="稳定标识" hint="系统内部使用">
          <Input value={form.category_key} onChange={(event) => setForm({ ...form, category_key: event.target.value })} placeholder="company_standards" />
        </Field>
        <Field label="显示编号"><Input value={form.display_code} onChange={(event) => setForm({ ...form, display_code: event.target.value })} placeholder="例如 03" /></Field>
        <Field label="分类名称"><Input value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} placeholder="例如 公司内部标准" /></Field>
        <Field label="父分类"><Select value={form.parent_id} onChange={(event) => setForm({ ...form, parent_id: event.target.value })}><option value="">一级分类</option>{categories.filter((item) => item.is_active && item.level < 4).map((item) => <option key={item.id} value={item.id}>{item.display_code} {item.display_name}</option>)}</Select></Field>
        <Button className="w-full" onClick={() => void create()} disabled={saving || !form.category_key.trim() || !form.display_code.trim() || !form.display_name.trim()}><Plus className="size-4" />{saving ? "新增中…" : "新增分类"}</Button>
      </div>
    </section>

    <section className="order-1 space-y-3 lg:order-2" aria-labelledby="category-list-title">
      <div className="flex items-center justify-between gap-3"><h2 id="category-list-title" className="text-ui-base font-semibold">现有分类</h2>{!loading && !error && <span className="text-ui-xs text-muted-foreground">共 {categories.length} 个</span>}</div>
      {loading ? <LoadingState className="min-h-48 border border-border" label="正在加载分类…" /> : !error && categories.length === 0 ? <EmptyState title="暂无分类" description="新增第一个分类后，可在此维护名称、编号和状态。" /> : !error && <div className="divide-y divide-border border-y border-border">{categories.map((category) => <CategoryEditor key={category.id} category={category} onSaved={() => load(true)} />)}</div>}
    </section>

    {isAdmin && <section className="order-3 space-y-3" aria-labelledby="content-permissions-title">
      <div><h2 id="content-permissions-title" className="text-ui-base font-semibold">资料权限</h2><p className="mt-1 text-ui-xs text-muted-foreground">权限独立保存，不会更改用户的全局角色。</p></div>
      {!loading && !error && permissionUsers.length === 0 ? <EmptyState title="暂无可配置用户" description="当前没有可配置资料权限的用户。" /> : !loading && !error && <div className="divide-y divide-border border-y border-border">{permissionUsers.map((user) => <PermissionEditor key={user.user_id} user={user} onSaved={() => load(true)} />)}</div>}
    </section>}
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
      <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="font-medium">{category.display_code} {category.display_name}</h3><Badge variant={active ? "success" : "secondary"}>{active ? "启用" : "停用"}</Badge><Badge variant="outline">第 {category.level} 级</Badge></div><p className="mt-1 break-all text-ui-xs text-muted-foreground">稳定标识：<span className="font-mono">{category.category_key}</span></p></div>
      <Button size="sm" variant="outline" className="w-full sm:w-auto" onClick={() => void save()} disabled={saving || !code.trim() || !name.trim()}><Save className="size-4" />{saving ? "保存中…" : "保存"}</Button>
    </div>
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[10rem_minmax(12rem,1fr)_8rem_auto] lg:items-end">
      <Field label="编号"><Input aria-label={`编辑${category.display_name}的编号`} value={code} onChange={(event) => setCode(event.target.value)} /></Field>
      <Field label="显示名称"><Input aria-label={`编辑${category.display_name}的显示名称`} value={name} onChange={(event) => setName(event.target.value)} /></Field>
      <Field label="排序"><Input aria-label={`编辑${category.display_name}的排序`} type="number" value={sortOrder} onChange={(event) => setSortOrder(event.target.value)} /></Field>
      <label className="flex h-control-md cursor-pointer items-center gap-2 text-ui-sm font-medium"><Checkbox aria-label={`${category.display_name}启用`} checked={active} onChange={(event) => setActive(event.target.checked)} />启用分类</label>
    </div>
  </article>;
}

const permissionColumns: [ContentPermission, string][] = [["organize", "整理"], ["review", "确认"], ["publish", "发布"], ["manage_categories", "分类管理"], ["import_server", "后台导入"]];

function PermissionEditor({ user, onSaved }: { user: ContentPermissionUser; onSaved: () => Promise<void> }) {
  const [permissions, setPermissions] = useState<ContentPermission[]>(user.permissions);
  const [savingPermission, setSavingPermission] = useState<ContentPermission | null>(null);

  const toggle = async (permission: ContentPermission) => {
    if (user.role === "admin" || !user.is_active || savingPermission) return;
    const previous = permissions;
    const next = previous.includes(permission) ? previous.filter((item) => item !== permission) : [...previous, permission];
    setPermissions(next);
    setSavingPermission(permission);
    try {
      await api.updateManagedContentPermissions(user.user_id, next);
      toast.success(`${user.real_name}的资料权限已保存`);
      await onSaved();
    } catch (saveError) {
      setPermissions(previous);
      toast.error(saveError instanceof Error ? saveError.message : "权限保存失败");
    } finally { setSavingPermission(null); }
  };

  const disabled = user.role === "admin" || !user.is_active;
  const disabledReason = user.role === "admin" ? "管理员默认拥有全部权限" : !user.is_active ? "账号已停用，无法修改权限" : null;
  return <article className="space-y-3 py-4">
    <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between"><div><h3 className="font-medium">{user.real_name}</h3><p className="text-ui-xs text-muted-foreground">{user.employee_id}</p></div><p className="min-h-5 text-ui-xs text-muted-foreground" role="status" aria-live="polite">{savingPermission ? `正在保存${permissionColumns.find(([key]) => key === savingPermission)?.[1]}权限…` : disabledReason}</p></div>
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">{permissionColumns.map(([permission, label]) => <label key={permission} className="flex min-h-control-md cursor-pointer items-center gap-2 rounded-ui-md border border-border px-3 py-2 text-ui-sm has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60"><Checkbox aria-label={`${user.real_name}${label}`} checked={user.role === "admin" || permissions.includes(permission)} disabled={disabled || Boolean(savingPermission)} onChange={() => void toggle(permission)} />{label}</label>)}</div>
  </article>;
}
