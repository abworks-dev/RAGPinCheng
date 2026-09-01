import type { ReactNode } from "react";
import { Checkbox } from "../ui/checkbox";

/**
 * 资料管理“资料列表”（普通目录）与“共享目录”共用同一套表格列几何，
 * 避免两处各自复制布局导致列位随内容漂移、多选框/表头不对齐。
 *
 * 表格使用 `table-fixed` + 除“资料”列外全部显式列宽：任意视图下列的
 * 起始位置保持一致；“资料”列吸收剩余宽度并允许长文本换行。
 */
export const managedTableClass =
  "w-full min-w-[72rem] table-fixed text-ui-sm";
export const managedTableHeaderRowClass =
  "border-b border-border bg-surface-muted text-left text-muted-foreground";

export const MANAGED_COLUMN_CLASS = {
  checkboxTh: "w-8 px-1.5 py-3",
  checkboxTd: "px-1.5 py-3",
  typeTh: "w-20 px-1 py-3 text-center font-medium",
  typeTd: "px-1 py-3",
  titleTh: "px-1.5 py-3 font-medium",
  titleTd: "px-1.5 py-3",
  updatedAtTh: "w-40 px-3 py-3 font-medium",
  updatedAtTd: "w-40 whitespace-nowrap px-3 py-3 tabular-nums",
  statusTh: "w-28 px-3 py-3 font-medium",
  statusTd: "w-28 px-3 py-3",
  sourceTh: "w-24 px-3 py-3 font-medium",
  sourceTd: "w-24 px-3 py-3",
  actionsTh: "w-[24rem] px-3 py-3 text-right font-medium",
  actionsTd: "w-[24rem] px-3 py-3 text-right",
} as const;

export type ManagedColumnKey =
  | "docType"
  | "title"
  | "updatedAt"
  | "status"
  | "source";

export type ManagedColumnSort = {
  key: ManagedColumnKey;
  direction: "asc" | "desc";
} | null;

function columnThClass(key: ManagedColumnKey) {
  switch (key) {
    case "docType":
      return MANAGED_COLUMN_CLASS.typeTh;
    case "title":
      return MANAGED_COLUMN_CLASS.titleTh;
    case "updatedAt":
      return MANAGED_COLUMN_CLASS.updatedAtTh;
    case "status":
      return MANAGED_COLUMN_CLASS.statusTh;
    default:
      return MANAGED_COLUMN_CLASS.sourceTh;
  }
}

const MANAGED_COLUMN_LABELS: [ManagedColumnKey, string][] = [
  ["docType", "类型"],
  ["title", "资料"],
  ["updatedAt", "更新时间"],
  ["status", "状态"],
  ["source", "来源"],
];

export function ManagedTableHeader({
  selectAllLabel,
  selectAllChecked = false,
  onToggleSelectAll,
  selectAllDisabled = false,
  sort = null,
  onToggleSort,
  renderSortIcon,
}: {
  selectAllLabel: string;
  selectAllChecked?: boolean;
  onToggleSelectAll?: () => void;
  selectAllDisabled?: boolean;
  sort?: ManagedColumnSort;
  onToggleSort?: (key: ManagedColumnKey) => void;
  renderSortIcon?: (key: ManagedColumnKey) => ReactNode;
}) {
  return (
    <thead className={managedTableHeaderRowClass}>
      <tr>
        <th className={MANAGED_COLUMN_CLASS.checkboxTh} scope="col">
          <Checkbox
            aria-label={selectAllLabel}
            checked={selectAllChecked}
            disabled={selectAllDisabled}
            onChange={
              onToggleSelectAll ? () => onToggleSelectAll() : undefined
            }
          />
        </th>
        {MANAGED_COLUMN_LABELS.map(([key, label]) => {
          const sortable = Boolean(onToggleSort);
          const ariaSort =
            sort?.key === key
              ? sort.direction === "asc"
                ? "ascending"
                : "descending"
              : "none";
          return (
            <th
              key={key}
              scope="col"
              aria-sort={sortable ? ariaSort : undefined}
              className={columnThClass(key)}
            >
              {sortable ? (
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded px-1 py-0.5 hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => onToggleSort!(key)}
                >
                  {label}
                  {renderSortIcon?.(key)}
                </button>
              ) : (
                label
              )}
            </th>
          );
        })}
        <th scope="col" className={MANAGED_COLUMN_CLASS.actionsTh}>
          操作
        </th>
      </tr>
    </thead>
  );
}