import type { ManagedCategory } from "../types";

export type CategoryTreeNode = {
  category: ManagedCategory;
  children: CategoryTreeNode[];
};

export function compareManagedCategories(left: ManagedCategory, right: ManagedCategory) {
  return left.display_code.localeCompare(right.display_code, "zh-Hans")
    || left.display_name.localeCompare(right.display_name, "zh-Hans")
    || left.id.localeCompare(right.id);
}

export function buildCategoryTree(categories: ManagedCategory[]): CategoryTreeNode[] {
  const children = new Map<string | null, ManagedCategory[]>();
  categories.forEach((category) => {
    const siblings = children.get(category.parent_id) || [];
    siblings.push(category);
    children.set(category.parent_id, siblings);
  });
  const createNode = (category: ManagedCategory): CategoryTreeNode => ({
    category,
    children: (children.get(category.id) || []).sort(compareManagedCategories).map(createNode),
  });
  return (children.get(null) || []).sort(compareManagedCategories).map(createNode);
}

export function filterCategoryTree(
  nodes: CategoryTreeNode[],
  matches: (category: ManagedCategory) => boolean,
): CategoryTreeNode[] {
  return nodes.flatMap((node) => {
    const children = filterCategoryTree(node.children, matches);
    return matches(node.category) || children.length > 0 ? [{ ...node, children }] : [];
  });
}

export function flattenVisibleCategoryTree(nodes: CategoryTreeNode[], expanded: Set<string>): CategoryTreeNode[] {
  return nodes.flatMap((node) => [
    node,
    ...(expanded.has(node.category.id) ? flattenVisibleCategoryTree(node.children, expanded) : []),
  ]);
}

export function collectExpandableCategoryIds(nodes: CategoryTreeNode[]): string[] {
  return nodes.flatMap((node) => node.children.length > 0
    ? [node.category.id, ...collectExpandableCategoryIds(node.children)]
    : []);
}

export function countCategoryTreeNodes(nodes: CategoryTreeNode[]): number {
  return nodes.reduce((count, node) => count + 1 + countCategoryTreeNodes(node.children), 0);
}

export function collectCategoryAncestorIds(categories: ManagedCategory[], categoryId: string | null): string[] {
  const byId = new Map(categories.map((category) => [category.id, category]));
  const result: string[] = [];
  let current = categoryId ? byId.get(categoryId) : undefined;
  while (current?.parent_id) {
    result.push(current.parent_id);
    current = byId.get(current.parent_id);
  }
  return result;
}
