import { Copy, KeyRound, MoreHorizontal, Plus, Settings, Shield, ShieldCheck, UserCheck, UserRound, UserX, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import { Select } from "../../components/ui/select";
import { toast } from "../../components/ui/toast";
import { cn } from "../../lib/utils";
import type { AdminConversation, AdminUser, ContentPermission, ContentPermissionGroup, ConversationState } from "../../types";
import { formatAdminDate } from "./admin-formatters";

const roleLabels: Record<ConversationState["messages"][number]["role"], string> = {
  user: "用户",
  assistant: "助手",
  system: "系统",
};

const permissionOptions: { key: ContentPermission; label: string; description: string }[] = [
  { key: "organize", label: "整理、上传", description: "上传资料并提交确认" },
  { key: "review", label: "确认", description: "确认或退回待审资料" },
  { key: "publish", label: "发布", description: "发布资料正式版本" },
  { key: "manage_categories", label: "分类管理", description: "维护资料分类与层级" },
  { key: "import_server", label: "后台导入", description: "执行服务器批次导入" },
];

const samePermissions = (left: ContentPermission[], right: ContentPermission[]) =>
  left.length === right.length && left.every((item) => right.includes(item));

export function AdminUsersPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [drillUser, setDrillUser] = useState<AdminUser | null>(null);
  const [filter, setFilter] = useState("");
  const [groups, setGroups] = useState<ContentPermissionGroup[]>([]);
  const [permissionUser, setPermissionUser] = useState<AdminUser | null>(null);
  const [managingGroups, setManagingGroups] = useState(false);

  // Filter on both real_name and employee_id since they live in the same
  // column visually and admins will sometimes search by either.
  const visibleUsers = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return users;
    return users.filter(
      (u) =>
        u.real_name.toLowerCase().includes(q) ||
        u.employee_id.toLowerCase().includes(q),
    );
  }, [users, filter]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [{ users }, permissionGroups] = await Promise.all([
        api.adminListUsers(),
        api.managedContentPermissionGroups(),
      ]);
      setUsers(users);
      setGroups(permissionGroups);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function toggleActive(u: AdminUser) {
    try {
      await api.adminPatchUser(u.id, { is_active: !u.is_active });
      refresh();
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }

  async function toggleRole(u: AdminUser) {
    const newRole = u.role === "admin" ? "user" : "admin";
    if (!confirm(`将 ${u.real_name}（${u.employee_id}）的角色改为 ${newRole}？`)) return;
    try {
      await api.adminPatchUser(u.id, { role: newRole });
      refresh();
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }

  async function resetPw(u: AdminUser) {
    const pw = prompt(`为 ${u.real_name}（${u.employee_id}）设置新密码（≥ 6 位）：`);
    if (!pw) return;
    if (pw.length < 6) {
      alert("密码至少 6 位");
      return;
    }
    try {
      await api.adminPatchUser(u.id, { reset_password: pw });
      alert("密码已重置；该用户的所有会话已失效。");
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }

  return (
    <section className="space-y-5" aria-labelledby="admin-users-title">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div><p className="text-ui-xs font-medium uppercase tracking-[0.14em] text-primary">账号与权限</p>
          <h1 id="admin-users-title" className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">用户管理</h1>
          <p className="mt-1 text-ui-sm text-muted-foreground">查看账号状态、资料权限与使用情况，并执行受控的管理员操作。</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => { setPermissionUser(null); setManagingGroups(true); }}><Settings className="size-4" />权限组管理</Button>
      </header>

      <Card className="shadow-surface">
        <CardContent className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between sm:p-4">
          <div className="w-full sm:max-w-xl">
            <label htmlFor="user-filter" className="sr-only">
              筛选用户
            </label>
            <Input
              id="user-filter"
              type="search"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="输入姓名或用户名…"
            />
          </div>
          <div className="flex min-h-control-md shrink-0 items-center gap-3">
            <span className="text-ui-xs text-muted-foreground" aria-live="polite">
              {filter ? `显示 ${visibleUsers.length} / ${users.length} 位` : `共 ${users.length} 位用户`}
            </span>
            {filter && (
              <Button variant="ghost" size="sm" onClick={() => setFilter("")}>
                清空筛选
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {error ? (
        <ErrorState title="用户列表加载失败" description={error} />
      ) : loading ? (
        <Card>
          <LoadingState className="min-h-56" label="正在加载用户…" />
        </Card>
      ) : users.length === 0 ? (
        <EmptyState title="暂无用户" description="当前还没有可供管理员查看的用户账号。" />
      ) : visibleUsers.length === 0 ? (
        <EmptyState
          title="没有匹配的用户"
          description={`没有找到与“${filter}”匹配的姓名或用户名。`}
          action={
            <Button variant="outline" size="sm" onClick={() => setFilter("")}>
              清空筛选
            </Button>
          }
        />
      ) : (
        <Card className="overflow-hidden shadow-surface">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[58rem] text-ui-sm">
              <caption className="sr-only">用户账号、角色、状态、使用情况和管理员操作</caption>
              <thead className="border-b border-border bg-surface-muted text-muted-foreground">
                <tr>
                  <th scope="col" className="px-4 py-3 text-left font-medium">用户</th>
                  <th scope="col" className="px-4 py-3 text-left font-medium">角色</th>
                  <th scope="col" className="px-4 py-3 text-left font-medium">状态</th>
                  <th scope="col" className="px-4 py-3 text-left font-medium">资料权限</th>
                  <th scope="col" className="px-4 py-3 text-right font-medium">对话</th>
                  <th scope="col" className="hidden px-4 py-3 text-left font-medium lg:table-cell">最近登录</th>
                  <th scope="col" className="hidden px-4 py-3 text-left font-medium xl:table-cell">注册时间</th>
                  <th scope="col" className="w-20 px-4 py-3 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {visibleUsers.map((user) => (
                  <tr key={user.id} className="bg-card transition-colors duration-normal hover:bg-surface-muted/60">
                    <td className="px-4 py-3">
                      <div className="font-medium text-foreground">{user.real_name}</div>
                      <div className="mt-0.5 font-mono text-ui-xs text-muted-foreground">{user.employee_id}</div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={user.role === "admin" ? "info" : "secondary"}>
                        {user.role === "admin" ? "管理员" : "用户"}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={user.is_active ? "success" : "destructive"}>
                        {user.is_active ? "启用" : "已停用"}
                      </Badge>
                    </td>
                    <td className="max-w-56 px-4 py-3"><PermissionSummary user={user} groups={groups} /></td>
                    <td className="px-4 py-3 text-right">
                      <Button
                        variant="link"
                        size="sm"
                        className="h-auto px-0"
                        aria-label={`查看 ${user.real_name} 的 ${user.conversation_count} 条对话`}
                        onClick={() => setDrillUser(user)}
                      >
                        {user.conversation_count} 条
                      </Button>
                    </td>
                    <td className="hidden whitespace-nowrap px-4 py-3 text-muted-foreground lg:table-cell">
                      {formatAdminDate(user.last_login_at)}
                    </td>
                    <td className="hidden whitespace-nowrap px-4 py-3 text-muted-foreground xl:table-cell">
                      {formatAdminDate(user.created_at)}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <UserActionsMenu
                        user={user}
                        onToggleActive={() => toggleActive(user)}
                        onToggleRole={() => toggleRole(user)}
                        onResetPassword={() => resetPw(user)}
                        onSetPermissions={() => { setManagingGroups(false); setPermissionUser(user); }}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {drillUser && (
        <UserConversationsDrillIn
          user={drillUser}
          onClose={() => setDrillUser(null)}
        />
      )}
      <PermissionDialog user={permissionUser} groups={groups.filter((group) => group.is_active)} onClose={() => setPermissionUser(null)} onSaved={refresh} />
      <PermissionGroupsDialog open={managingGroups} groups={groups} onOpenChange={setManagingGroups} onSaved={refresh} />
    </section>
  );
}

function PermissionSummary({ user, groups }: { user: AdminUser; groups: ContentPermissionGroup[] }) {
  if (user.role === "admin") return <div className="space-y-1"><Badge variant="info">系统管理员 · 全部权限</Badge><p className="text-ui-xs text-muted-foreground">完整管理工作台</p></div>;
  const permissions = user.content_permissions || [];
  const matched = groups.find((group) => group.is_active && samePermissions(group.permissions, permissions));
  if (matched) return <div className="space-y-1"><Badge variant={permissions.length ? "outline" : "secondary"}>{matched.display_name}</Badge><p className="text-ui-xs text-muted-foreground">{permissions.length ? "可进入资料工作台" : "无工作台权限"}</p></div>;
  if (!permissions.length) return <div className="space-y-1"><Badge variant="secondary">无资料权限</Badge><p className="text-ui-xs text-muted-foreground">无工作台权限</p></div>;
  const labels = permissionOptions.filter((item) => permissions.includes(item.key)).map((item) => item.label);
  return <div className="space-y-1"><div className="flex flex-wrap items-center gap-1.5"><Badge variant="outline">自定义</Badge><span className="text-ui-xs text-muted-foreground" title={labels.join("、")}>{labels.slice(0, 2).join("、")}{labels.length > 2 ? ` +${labels.length - 2}` : ""}</span></div><p className="text-ui-xs text-muted-foreground">可进入资料工作台</p></div>;
}

function PermissionDialog({ user, groups, onClose, onSaved }: { user: AdminUser | null; groups: ContentPermissionGroup[]; onClose: () => void; onSaved: () => Promise<void> }) {
  const [permissions, setPermissions] = useState<ContentPermission[]>([]);
  const [saving, setSaving] = useState(false);
  useEffect(() => { setPermissions(user?.content_permissions || []); }, [user]);
  if (!user) return null;
  const disabled = user.role === "admin" || !user.is_active;
  const matched = groups.find((group) => samePermissions(group.permissions, permissions));
  const changed = !samePermissions(permissions, user.content_permissions || []);
  const save = async () => {
    if (saving) return;
    setSaving(true);
    try {
      await api.updateManagedContentPermissions(user.id, permissions);
      await onSaved();
      toast.success(`${user.real_name}的资料权限已保存`);
      onClose();
    } catch (saveError) {
      toast.error(saveError instanceof Error ? saveError.message : "权限保存失败");
    } finally { setSaving(false); }
  };
  return <Dialog open onOpenChange={(open) => { if (!open && !saving) onClose(); }}>
    <DialogContent className="max-h-[calc(100vh-2rem)] max-w-2xl overflow-y-auto">
      <DialogHeader><DialogTitle>设置资料权限</DialogTitle><DialogDescription>{user.real_name} · {user.employee_id}</DialogDescription></DialogHeader>
      <div className="space-y-5">
        <label className="block space-y-1.5 text-ui-sm font-medium">权限组
          <Select aria-label="选择权限组" value={matched?.id || "custom"} disabled={disabled || saving} onChange={(event) => {
            const group = groups.find((item) => item.id === event.target.value);
            if (group) setPermissions(group.permissions);
          }}>
            {groups.map((group) => <option key={group.id} value={group.id}>{group.display_name}</option>)}
            <option value="custom">自定义配置</option>
          </Select>
        </label>
        <fieldset disabled={disabled || saving} className="space-y-2"><legend className="mb-2 text-ui-sm font-medium">实际权限</legend>
          <div className="grid gap-2 sm:grid-cols-2">{permissionOptions.map((option) => <label key={option.key} className="flex min-h-16 cursor-pointer items-start gap-3 rounded-ui-md border border-border p-3 has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60">
            <Checkbox className="mt-0.5" checked={user.role === "admin" || permissions.includes(option.key)} onChange={(event) => setPermissions((current) => event.target.checked ? [...current, option.key] : current.filter((item) => item !== option.key))} />
            <span><span className="block text-ui-sm font-medium">{option.label}</span><span className="mt-0.5 block text-ui-xs text-muted-foreground">{option.description}</span></span>
          </label>)}</div>
        </fieldset>
        <div className="rounded-ui-md bg-surface-muted px-3 py-2 text-ui-xs text-muted-foreground" role="status">
          {user.role === "admin" ? "管理员默认拥有全部权限和完整管理工作台，不能单独取消。" : !user.is_active ? "账号已停用，权限保留但暂不能修改。" : permissions.length ? `保存后可进入资料工作台，并拥有：${permissionOptions.filter((item) => permissions.includes(item.key)).map((item) => item.label).join("、")}` : "保存后将不拥有资料权限，也不能进入资料工作台。"}
        </div>
      </div>
      <DialogFooter><Button variant="outline" onClick={onClose} disabled={saving}>取消</Button><Button onClick={() => void save()} disabled={disabled || saving || !changed}>{saving ? "保存中…" : "保存权限"}</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}

function PermissionGroupsDialog({ open, groups, onOpenChange, onSaved }: { open: boolean; groups: ContentPermissionGroup[]; onOpenChange: (open: boolean) => void; onSaved: () => Promise<void> }) {
  const [selectedId, setSelectedId] = useState("");
  const [name, setName] = useState("");
  const [permissions, setPermissions] = useState<ContentPermission[]>([]);
  const [saving, setSaving] = useState(false);
  const selected = groups.find((group) => group.id === selectedId);
  useEffect(() => {
    if (!open) return;
    const first = groups[0];
    setSelectedId(first?.id || "new"); setName(first?.display_name || ""); setPermissions(first?.permissions || []);
  }, [open, groups]);
  const choose = (id: string) => {
    setSelectedId(id);
    const group = groups.find((item) => item.id === id);
    setName(group?.display_name || ""); setPermissions(group?.permissions || []);
  };
  const save = async () => {
    if (saving) return;
    setSaving(true);
    try {
      if (selected) await api.updateManagedContentPermissionGroup(selected.id, { display_name: name.trim(), permissions });
      else await api.createManagedContentPermissionGroup({ display_name: name.trim(), permissions });
      await onSaved(); toast.success(selected ? "权限组已保存" : "权限组已创建");
    } catch (saveError) { toast.error(saveError instanceof Error ? saveError.message : "权限组保存失败"); }
    finally { setSaving(false); }
  };
  const copy = () => { setSelectedId("new"); setName(`${selected?.display_name || "权限组"}副本`); setPermissions(selected?.permissions || []); };
  const deactivate = async () => {
    if (!selected || selected.is_system || saving) return;
    setSaving(true);
    try { await api.updateManagedContentPermissionGroup(selected.id, { is_active: !selected.is_active }); await onSaved(); toast.success(selected.is_active ? "权限组已停用" : "权限组已启用"); }
    catch (saveError) { toast.error(saveError instanceof Error ? saveError.message : "权限组状态保存失败"); }
    finally { setSaving(false); }
  };
  return <Dialog open={open} onOpenChange={(next) => { if (!saving) onOpenChange(next); }}><DialogContent className="max-h-[calc(100vh-2rem)] max-w-3xl overflow-y-auto">
    <DialogHeader><DialogTitle>权限组管理</DialogTitle><DialogDescription>权限组是配置模板；修改模板不会改变既有用户权限。</DialogDescription></DialogHeader>
    <div className="grid gap-5 md:grid-cols-[15rem_minmax(0,1fr)]">
      <div className="space-y-2"><Button variant="outline" className="w-full justify-start" onClick={() => choose("new")}><Plus className="size-4" />新建权限组</Button>
        <div className="divide-y divide-border border-y border-border">{groups.map((group) => <button type="button" key={group.id} onClick={() => choose(group.id)} className={cn("flex w-full items-center justify-between gap-2 px-2 py-2.5 text-left text-ui-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", selectedId === group.id && "bg-primary/10")}><span>{group.display_name}</span><Badge variant={group.is_active ? "outline" : "secondary"}>{group.is_system ? "预设" : group.is_active ? "自定义" : "停用"}</Badge></button>)}</div>
      </div>
      <div className="space-y-4"><label className="block space-y-1.5 text-ui-sm font-medium">权限组名称<Input value={name} disabled={selected?.is_system || saving} onChange={(event) => setName(event.target.value)} /></label>
        <fieldset disabled={selected?.is_system || saving} className="space-y-2"><legend className="text-ui-sm font-medium">模板权限</legend>{permissionOptions.map((option) => <label key={option.key} className="flex min-h-control-md items-center gap-2 rounded-ui-md border border-border px-3 py-2 text-ui-sm has-[:disabled]:opacity-60"><Checkbox checked={permissions.includes(option.key)} onChange={(event) => setPermissions((current) => event.target.checked ? [...current, option.key] : current.filter((item) => item !== option.key))} />{option.label}</label>)}</fieldset>
        <div className="flex flex-wrap justify-between gap-2"><div>{selected && <Button variant="outline" onClick={copy} disabled={saving}><Copy className="size-4" />复制</Button>}</div><div className="flex gap-2">{selected && !selected.is_system && <Button variant="outline" onClick={() => void deactivate()} disabled={saving}>{selected.is_active ? "停用" : "启用"}</Button>}<Button onClick={() => void save()} disabled={saving || selected?.is_system || name.trim().length < 2}>{saving ? "保存中…" : selected ? "保存模板" : "创建模板"}</Button></div></div>
      </div>
    </div>
  </DialogContent></Dialog>;
}

function UserActionsMenu({
  user,
  onToggleActive,
  onToggleRole,
  onResetPassword,
  onSetPermissions,
}: {
  user: AdminUser;
  onToggleActive: () => void;
  onToggleRole: () => void;
  onResetPassword: () => void;
  onSetPermissions: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ top: 0, right: 0 });
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;

    const closeIfOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!triggerRef.current?.contains(target) && !menuRef.current?.contains(target)) {
        setOpen(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    const closeOnViewportChange = () => setOpen(false);

    document.addEventListener("mousedown", closeIfOutside);
    document.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", closeOnViewportChange);
    return () => {
      document.removeEventListener("mousedown", closeIfOutside);
      document.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", closeOnViewportChange);
    };
  }, [open]);

  function toggleMenu() {
    if (!open && triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      const estimatedMenuHeight = 188;
      setPosition({
        top: rect.bottom + estimatedMenuHeight + 12 <= window.innerHeight
          ? rect.bottom + 6
          : Math.max(12, rect.top - estimatedMenuHeight - 6),
        right: Math.max(12, window.innerWidth - rect.right),
      });
    }
    setOpen((value) => !value);
  }

  function run(action: () => void) {
    setOpen(false);
    action();
  }

  return (
    <>
      <Button
        ref={triggerRef}
        variant="ghost"
        size="icon"
        className="h-control-sm w-control-sm text-muted-foreground hover:text-foreground"
        aria-label={`管理 ${user.real_name}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={toggleMenu}
      >
        <MoreHorizontal className="size-4" aria-hidden="true" />
      </Button>
      {open &&
        createPortal(
          <div
            ref={menuRef}
            role="menu"
            aria-label={`${user.real_name}的账号操作`}
            className="fixed z-dropdown w-44 overflow-hidden rounded-ui-lg border border-border bg-popover p-1.5 text-left text-popover-foreground shadow-overlay"
            style={{ top: position.top, right: position.right }}
          >
            <button
              type="button" role="menuitem"
              className="flex w-full items-center gap-2.5 rounded-ui-md px-2.5 py-2 text-ui-sm transition-colors hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              onClick={() => run(onSetPermissions)}
            ><Shield className="size-4 text-muted-foreground" aria-hidden="true" />设置权限</button>
            <button
              type="button"
              role="menuitem"
              className="flex w-full items-center gap-2.5 rounded-ui-md px-2.5 py-2 text-ui-sm transition-colors hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              onClick={() => run(onToggleRole)}
            >
              {user.role === "admin" ? (
                <UserRound className="size-4 text-muted-foreground" aria-hidden="true" />
              ) : (
                <ShieldCheck className="size-4 text-muted-foreground" aria-hidden="true" />
              )}
              {user.role === "admin" ? "降为普通用户" : "设为管理员"}
            </button>
            <button
              type="button"
              role="menuitem"
              className="flex w-full items-center gap-2.5 rounded-ui-md px-2.5 py-2 text-ui-sm transition-colors hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              onClick={() => run(onResetPassword)}
            >
              <KeyRound className="size-4 text-muted-foreground" aria-hidden="true" />
              重置密码
            </button>
            <div className="my-1 border-t border-border" role="separator" />
            <button
              type="button"
              role="menuitem"
              className={cn(
                "flex w-full items-center gap-2.5 rounded-ui-md px-2.5 py-2 text-ui-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                user.is_active
                  ? "text-destructive hover:bg-destructive/10"
                  : "text-success hover:bg-success/10",
              )}
              onClick={() => run(onToggleActive)}
            >
              {user.is_active ? (
                <UserX className="size-4" aria-hidden="true" />
              ) : (
                <UserCheck className="size-4" aria-hidden="true" />
              )}
              {user.is_active ? "停用账号" : "启用账号"}
            </button>
          </div>,
          document.body,
        )}
    </>
  );
}

function UserConversationsDrillIn({
  user,
  onClose,
}: {
  user: AdminUser;
  onClose: () => void;
}) {
  const [list, setList] = useState<AdminConversation[]>([]);
  const [selected, setSelected] = useState<ConversationState | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const { conversations } = await api.adminListUserConversations(user.id);
        setList(conversations);
      } finally {
        setLoading(false);
      }
    })();
  }, [user.id]);

  return (
    <div className="fixed inset-0 z-modal flex items-stretch justify-center bg-black/50 p-3 backdrop-blur-sm sm:p-6">
      <Card
        className="flex max-h-[calc(100vh-1.5rem)] w-full max-w-6xl flex-col overflow-hidden shadow-overlay sm:max-h-[calc(100vh-3rem)]"
        role="dialog"
        aria-modal="true"
        aria-labelledby="user-conversations-title"
      >
        <div className="flex items-center justify-between gap-4 border-b border-border px-4 py-3 sm:px-5">
          <div className="min-w-0">
            <h2 id="user-conversations-title" className="truncate text-ui-base font-semibold text-foreground">
              {user.real_name}的对话
            </h2>
            <p className="truncate text-ui-xs text-muted-foreground">用户名 {user.employee_id} · 只读查看</p>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="关闭用户对话">
            <X className="size-4" aria-hidden="true" />
          </Button>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[18rem_minmax(0,1fr)]">
          <div className="max-h-56 overflow-y-auto border-b border-border md:max-h-none md:border-b-0 md:border-r">
            {loading ? (
              <LoadingState className="min-h-32" label="正在加载对话…" />
            ) : list.length === 0 ? (
              <EmptyState className="m-3 border-0 bg-surface-muted" title="暂无对话" description="该用户尚无对话记录。" />
            ) : (
              <div className="divide-y divide-border" role="list">
                {list.map((conversation) => (
                  <div key={conversation.id} role="listitem">
                    <button
                      type="button"
                      onClick={async () => {
                        try {
                          const state = await api.adminGetConversation(conversation.id);
                          setSelected(state);
                        } catch (e: any) {
                          alert(e?.message || String(e));
                        }
                      }}
                      className={cn(
                        "w-full border-l-2 px-4 py-3 text-left transition-colors duration-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                        selected?.id === conversation.id
                          ? "border-l-primary bg-primary/10"
                          : "border-l-transparent hover:bg-surface-muted",
                      )}
                    >
                      <p className="truncate text-ui-sm font-medium text-foreground">{conversation.title}</p>
                      <p className="mt-1 text-ui-xs text-muted-foreground">{formatAdminDate(conversation.updated_at)}</p>
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="min-h-0 overflow-y-auto p-4 sm:p-5">
            {!selected ? (
              <EmptyState
                className="min-h-56 border-0 bg-surface-muted"
                title="选择一条对话"
                description="从列表选择对话后，可在这里查看完整消息。"
              />
            ) : (
              <div className="space-y-4">
                <div className="border-b border-border pb-3">
                  <h3 className="text-ui-lg font-semibold text-foreground">{selected.title}</h3>
                  <p className="mt-1 text-ui-xs text-muted-foreground">{selected.turn_index} 轮对话</p>
                </div>
                {selected.messages.map((message, index) => {
                  const isUser = message.role === "user";
                  return (
                    <article
                      key={message.id ?? `${message.role}-${index}`}
                      className={cn(
                        "rounded-ui-xl border px-4 py-3",
                        isUser
                          ? "ml-auto w-fit max-w-[88%] border-primary/20 bg-primary/10"
                          : "mr-auto w-full max-w-3xl border-border bg-surface-muted",
                      )}
                    >
                      <Badge
                        variant={message.role === "user" ? "info" : message.role === "system" ? "warning" : "outline"}
                        className={message.role === "assistant" ? "border-border bg-card" : undefined}
                      >
                        {roleLabels[message.role]}
                      </Badge>
                      <p className="mt-2 max-w-[72ch] whitespace-pre-wrap break-words text-ui-sm leading-relaxed text-foreground">
                        {message.content}
                      </p>
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}
