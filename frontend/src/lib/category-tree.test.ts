import { describe, expect, it } from "vitest";
import type { ManagedCategory } from "../types";
import { compareManagedCategories } from "./category-tree";

const category = (id: string, displayName: string, sortOrder: number): ManagedCategory => ({
  id,
  category_key: id,
  parent_id: null,
  display_code: id,
  display_name: displayName,
  sort_order: sortOrder,
  level: 1,
  is_active: true,
  version: 1,
  created_at: 1,
  updated_at: 1,
  full_path: `${id} ${displayName}`,
  item_count: 0,
});

describe("compareManagedCategories", () => {
  it("matches the backend numeric display-code order", () => {
    const rows = [
      category("02", "第二项", 10),
      category("10", "编号靠后", 10),
      category("03", "第三项", 10),
      category("00", "未设置", 0),
      category("01", "第一项", 5),
    ];

    expect(rows.sort(compareManagedCategories).map((row) => row.id)).toEqual([
      "00",
      "01",
      "02",
      "03",
      "10",
    ]);
  });
});
