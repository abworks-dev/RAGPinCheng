import type { ManagedCategory } from "../types";

export type CategoryTreeNode = {
  category: ManagedCategory;
  children: CategoryTreeNode[];
};

function compareUnicodeCodePoints(left: string, right: string) {
  const leftPoints = Array.from(left.normalize("NFKC").trim(), (character) => character.codePointAt(0)!);
  const rightPoints = Array.from(right.normalize("NFKC").trim(), (character) => character.codePointAt(0)!);
  const sharedLength = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < sharedLength; index += 1) {
    if (leftPoints[index] !== rightPoints[index]) return leftPoints[index] - rightPoints[index];
  }
  return leftPoints.length - rightPoints.length;
}

export function compareManagedCategories(left: ManagedCategory, right: ManagedCategory) {
  const leftUnset = left.sort_order <= 0;
  const rightUnset = right.sort_order <= 0;
  if (leftUnset !== rightUnset) return leftUnset ? 1 : -1;
  return left.sort_order - right.sort_order
    || compareUnicodeCodePoints(left.display_name, right.display_name)
    || compareUnicodeCodePoints(left.id, right.id);
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
