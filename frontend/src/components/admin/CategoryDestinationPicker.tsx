import { useState } from "react";
import { FolderPlus, X } from "lucide-react";
import type { ManagedCategory } from "../../types";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { CategoryTreePicker } from "./CategoryTreePicker";

type Props = {
  categories: ManagedCategory[];
  value: string;
  onChange: (categoryId: string) => void;
  label?: string;
  currentCategoryId?: string | null;
  currentCategorySelectable?: boolean;
  disabled?: boolean;
  rootOption?: { value: string; label: string; description?: string; disabledReason?: string };
  disabledCategoryReasons?: Record<string, string>;
  onCreateFolder?: (parentCategoryId: string, displayName: string) => Promise<ManagedCategory>;
};

export function CategoryDestinationPicker({
  categories,
  value,
  onChange,
  onCreateFolder,
  ...pickerProps
}: Props) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const parent = categories.find((category) => category.id === value) || null;
  const canCreate = Boolean(onCreateFolder && parent && !pickerProps.disabled);

  const create = async () => {
    if (!onCreateFolder || !parent || !name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const category = await onCreateFolder(parent.id, name.trim());
      setName("");
      setCreating(false);
      onChange(category.id);
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : "新建文件夹失败");
    } finally {
      setBusy(false);
    }
  };

  return <div className="space-y-3">
    <CategoryTreePicker categories={categories} value={value} onChange={onChange} {...pickerProps} />
    {onCreateFolder && <div className="border-t border-border pt-3">
      {!creating ? <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={!canCreate}
        title={!parent ? "请先选择上级目录" : undefined}
        onClick={() => setCreating(true)}
      ><FolderPlus className="size-4" />新建文件夹</Button> : <div className="space-y-2">
        <div className="flex items-center gap-2">
          <Input
            value={name}
            maxLength={100}
            autoFocus
            aria-label="新文件夹名称"
            placeholder="文件夹名称"
            disabled={busy}
            onChange={(event) => { setName(event.target.value); setError(null); }}
            onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void create(); } }}
          />
          <Button type="button" size="sm" disabled={!name.trim() || busy} onClick={() => void create()}>{busy ? "创建中…" : "创建"}</Button>
          <Button type="button" size="icon" variant="ghost" title="取消新建" aria-label="取消新建" disabled={busy} onClick={() => { setCreating(false); setName(""); setError(null); }}><X className="size-4" /></Button>
        </div>
        {parent && <p className="break-words text-ui-xs text-muted-foreground">将在“{parent.full_path}”下创建并自动选中。</p>}
        {error && <p className="text-ui-sm text-destructive" role="alert">{error}</p>}
      </div>}
    </div>}
  </div>;
}
