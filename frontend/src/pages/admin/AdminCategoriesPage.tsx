import { useEffect, useState } from "react";
import { Plus, RefreshCw, Save } from "lucide-react";
import { api } from "../../api/client";
import type { ContentPermission, ContentPermissionUser, ManagedCategory } from "../../types";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Badge } from "../../components/ui/badge";
import { toast } from "../../components/ui/toast";
import { useAuth } from "../../context/AuthContext";

export function AdminCategoriesPage() {
  const { state } = useAuth();
  const isAdmin = state.status === "authed" && state.user.role === "admin";
  const [categories, setCategories] = useState<ManagedCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [permissionUsers, setPermissionUsers] = useState<ContentPermissionUser[]>([]);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ category_key: "", parent_id: "", display_code: "", display_name: "", sort_order: "0" });

  const load = async () => {
    setLoading(true);
    try {
      const [categoryRows, permissionRows] = await Promise.all([
        api.managedCategories(true),
        isAdmin ? api.managedContentPermissions() : Promise.resolve([]),
      ]);
      setCategories(categoryRows);
      setPermissionUsers(permissionRows);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "分类加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

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
      await load();
      toast.success("分类已创建");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "分类创建失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="space-y-6" aria-labelledby="managed-categories-title">
      <div className="flex items-end justify-between gap-4">
        <div>
          <p className="text-ui-xs text-muted-foreground">资料管理</p>
          <h1 id="managed-categories-title" className="mt-1 text-ui-2xl font-semibold">分类设置</h1>
        </div>
        <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
          <RefreshCw className="size-4" />刷新
        </Button>
      </div>

      <section className="border-y border-border py-5" aria-labelledby="new-category-title">
        <h2 id="new-category-title" className="mb-3 text-ui-base font-semibold">新增分类</h2>
        <div className="grid gap-3 md:grid-cols-5">
          <Input aria-label="分类标识" placeholder="category_key" value={form.category_key} onChange={(event) => setForm({ ...form, category_key: event.target.value })} />
          <Input aria-label="显示编号" placeholder="编号" value={form.display_code} onChange={(event) => setForm({ ...form, display_code: event.target.value })} />
          <Input aria-label="显示名称" placeholder="分类名称" value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} />
          <select aria-label="父分类" className="h-control-md rounded-ui-md border border-input bg-background px-3 text-ui-sm" value={form.parent_id} onChange={(event) => setForm({ ...form, parent_id: event.target.value })}>
            <option value="">一级分类</option>
            {categories.filter((item) => item.is_active && item.level < 4).map((item) => <option key={item.id} value={item.id}>{item.display_code} {item.display_name}</option>)}
          </select>
          <Button onClick={() => void create()} disabled={saving || !form.category_key.trim() || !form.display_code.trim() || !form.display_name.trim()}>
            <Plus className="size-4" />新增
          </Button>
        </div>
      </section>

      <div className="overflow-x-auto border border-border">
        <table className="min-w-[58rem] w-full text-ui-sm">
          <thead className="bg-surface-muted text-left text-muted-foreground"><tr><th className="px-3 py-2">层级</th><th className="px-3 py-2">稳定标识</th><th className="px-3 py-2">编号</th><th className="px-3 py-2">名称</th><th className="px-3 py-2">排序</th><th className="px-3 py-2">状态</th><th className="px-3 py-2 text-right">操作</th></tr></thead>
          <tbody className="divide-y divide-border">
            {categories.map((category) => <CategoryRow key={category.id} category={category} onSaved={load} />)}
          </tbody>
        </table>
        {!loading && categories.length === 0 && <p className="p-6 text-center text-muted-foreground">暂无分类</p>}
      </div>

      {isAdmin && <section className="space-y-3" aria-labelledby="content-permissions-title">
        <h2 id="content-permissions-title" className="text-ui-base font-semibold">资料权限</h2>
        <div className="overflow-x-auto border border-border"><table className="min-w-[44rem] w-full text-ui-sm"><thead className="bg-surface-muted text-left text-muted-foreground"><tr><th className="px-3 py-2">用户</th><th className="px-3 py-2">整理</th><th className="px-3 py-2">确认</th><th className="px-3 py-2">发布</th><th className="px-3 py-2">分类管理</th><th className="px-3 py-2">后台导入</th></tr></thead><tbody className="divide-y divide-border">{permissionUsers.map((user) => <PermissionRow key={user.user_id} user={user} onSaved={load} />)}</tbody></table></div>
      </section>}
    </section>
  );
}

const permissionColumns: [ContentPermission, string][] = [
  ["organize", "整理"], ["review", "确认"], ["publish", "发布"],
  ["manage_categories", "分类管理"], ["import_server", "后台导入"],
];

function PermissionRow({ user, onSaved }: { user: ContentPermissionUser; onSaved: () => Promise<void> }) {
  const [permissions, setPermissions] = useState<ContentPermission[]>(user.permissions);
  const toggle = async (permission: ContentPermission) => {
    if (user.role === "admin") return;
    const next = permissions.includes(permission) ? permissions.filter((item) => item !== permission) : [...permissions, permission];
    setPermissions(next);
    try { await api.updateManagedContentPermissions(user.user_id, next); await onSaved(); }
    catch (error) { setPermissions(user.permissions); toast.error(error instanceof Error ? error.message : "权限保存失败"); }
  };
  return <tr><td className="px-3 py-2"><p className="font-medium">{user.real_name}</p><p className="text-ui-xs text-muted-foreground">{user.employee_id}{user.role === "admin" ? " · 管理员默认拥有全部权限" : ""}</p></td>{permissionColumns.map(([permission, label]) => <td key={permission} className="px-3 py-2"><input type="checkbox" aria-label={`${user.real_name}${label}`} checked={user.role === "admin" || permissions.includes(permission)} disabled={user.role === "admin" || !user.is_active} onChange={() => void toggle(permission)} /></td>)}</tr>;
}

function CategoryRow({ category, onSaved }: { category: ManagedCategory; onSaved: () => Promise<void> }) {
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
      toast.success("分类已保存");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "分类保存失败");
    } finally { setSaving(false); }
  };

  return <tr>
    <td className="px-3 py-2">{category.level}</td>
    <td className="px-3 py-2 font-mono text-ui-xs">{category.category_key}</td>
    <td className="w-28 px-3 py-2"><Input aria-label={`${category.display_name}编号`} value={code} onChange={(event) => setCode(event.target.value)} /></td>
    <td className="min-w-52 px-3 py-2"><Input aria-label={`${category.display_name}名称`} value={name} onChange={(event) => setName(event.target.value)} /></td>
    <td className="w-28 px-3 py-2"><Input aria-label={`${category.display_name}排序`} type="number" value={sortOrder} onChange={(event) => setSortOrder(event.target.value)} /></td>
    <td className="px-3 py-2"><button type="button" onClick={() => setActive(!active)} aria-label={`${category.display_name}状态`}><Badge variant={active ? "success" : "secondary"}>{active ? "启用" : "停用"}</Badge></button></td>
    <td className="px-3 py-2 text-right"><Button size="sm" variant="outline" onClick={() => void save()} disabled={saving}><Save className="size-4" />保存</Button></td>
  </tr>;
}
