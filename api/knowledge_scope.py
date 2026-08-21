"""Resolve user-facing knowledge scopes into safe category keys."""
from __future__ import annotations

import sqlite3


def _rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT id,parent_id,category_key,display_code,display_name,full_path,level,
                  is_active,chat_search_enabled,chat_filter_selectable
           FROM (
             WITH RECURSIVE paths AS (
               SELECT id,parent_id,category_key,display_code,display_name,level,is_active,
                      chat_search_enabled,chat_filter_selectable,
                      display_code || ' ' || display_name AS full_path
               FROM category_nodes WHERE parent_id IS NULL AND deleted_at IS NULL
               UNION ALL
               SELECT c.id,c.parent_id,c.category_key,c.display_code,c.display_name,c.level,c.is_active,
                      c.chat_search_enabled,c.chat_filter_selectable,
                      p.full_path || ' / ' || c.display_code || ' ' || c.display_name
               FROM category_nodes c JOIN paths p ON p.id=c.parent_id
               WHERE c.deleted_at IS NULL
             )
             SELECT * FROM paths
           )
           ORDER BY full_path"""
    ).fetchall()


def resolve_category_scope(
    conn: sqlite3.Connection,
    category_ids: list[str] | None,
    *,
    legacy_categories: list[str] | None = None,
) -> list[str]:
    """Return only active, chat-enabled category keys, including descendants."""
    rows = _rows(conn)
    by_id = {str(row["id"]): row for row in rows}
    by_key = {str(row["category_key"]): row for row in rows}
    by_name = {str(row["display_name"]): row for row in rows}
    children: dict[str | None, list[sqlite3.Row]] = {}
    for row in rows:
        children.setdefault(row["parent_id"], []).append(row)

    effective: dict[str, bool] = {}
    filter_effective: dict[str, bool] = {}

    def is_effective(row: sqlite3.Row) -> bool:
        category_id = str(row["id"])
        if category_id in effective:
            return effective[category_id]
        parent_id = row["parent_id"]
        parent_available = parent_id is None or (
            str(parent_id) in by_id and is_effective(by_id[str(parent_id)])
        )
        available = bool(row["is_active"] and row["chat_search_enabled"] and parent_available)
        effective[category_id] = available
        return available

    def is_filter_effective(row: sqlite3.Row) -> bool:
        category_id = str(row["id"])
        if category_id in filter_effective:
            return filter_effective[category_id]
        parent_id = row["parent_id"]
        parent_available = parent_id is None or (
            str(parent_id) in by_id and is_filter_effective(by_id[str(parent_id)])
        )
        available = bool(is_effective(row) and row["chat_filter_selectable"] and parent_available)
        filter_effective[category_id] = available
        return available

    requested = category_ids if category_ids is not None else legacy_categories
    if requested is None:
        selected = [row for row in rows if is_effective(row)]
    else:
        selected = []
        seen: set[str] = set()
        for token in requested:
            value = str(token).strip()
            row = by_id.get(value) or by_key.get(value) or by_name.get(value)
            if row is None:
                raise ValueError("knowledge_scope_not_found")
            if not is_effective(row):
                raise ValueError("knowledge_scope_unavailable")
            if not is_filter_effective(row):
                raise ValueError("knowledge_scope_not_selectable")
            if row["id"] not in seen:
                selected.append(row)
                seen.add(row["id"])

    keys: set[str] = set()
    stack = list(selected)
    while stack:
        row = stack.pop()
        if is_effective(row):
            keys.add(str(row["category_key"]))
            stack.extend(children.get(row["id"], ()))
    return sorted(keys)


def list_knowledge_scopes(conn: sqlite3.Connection) -> list[dict[str, object]]:
    rows = _rows(conn)
    by_id = {str(row["id"]): row for row in rows}
    children: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        parent = row["parent_id"]
        if parent:
            children.setdefault(str(parent), []).append(row)

    effective: dict[str, bool] = {}
    filter_effective: dict[str, bool] = {}

    def is_effective(row: sqlite3.Row) -> bool:
        category_id = str(row["id"])
        if category_id in effective:
            return effective[category_id]
        parent_id = row["parent_id"]
        parent_available = parent_id is None or (
            str(parent_id) in by_id and is_effective(by_id[str(parent_id)])
        )
        available = bool(row["is_active"] and row["chat_search_enabled"] and parent_available)
        effective[category_id] = available
        return available

    def is_filter_effective(row: sqlite3.Row) -> bool:
        category_id = str(row["id"])
        if category_id in filter_effective:
            return filter_effective[category_id]
        parent_id = row["parent_id"]
        parent_available = parent_id is None or (
            str(parent_id) in by_id and is_filter_effective(by_id[str(parent_id)])
        )
        available = bool(is_effective(row) and row["chat_filter_selectable"] and parent_available)
        filter_effective[category_id] = available
        return available

    def count_descendants(category_id: str) -> int:
        total = 0
        for child in children.get(category_id, ()):
            if is_effective(child):
                total += 1
            total += count_descendants(str(child["id"]))
        return total

    return [
        {
            "id": str(row["id"]),
            "parent_id": row["parent_id"],
            "display_code": str(row["display_code"]),
            "display_name": str(row["display_name"]),
            "full_path": str(row["full_path"]),
            "level": int(row["level"]),
            "descendant_count": count_descendants(str(row["id"])),
            "chat_search_enabled": bool(row["chat_search_enabled"]),
            "chat_filter_selectable": bool(row["chat_filter_selectable"]),
        }
        for row in rows
        if is_filter_effective(row)
    ]
