from __future__ import annotations

import sqlite3

import pytest

from api.db import init_db
from api.knowledge_scope import list_knowledge_scopes, resolve_category_scope
from src import session as session_module


def _db(tmp_path) -> sqlite3.Connection:
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """INSERT INTO category_nodes
           (id,category_key,parent_id,display_code,display_name,sort_order,level,is_active,
            chat_search_enabled,chat_filter_selectable,created_at,updated_at)
           VALUES ('cat-child','company_child','cat-03','01','企业制度',10,2,1,1,1,1,1)"""
    )
    conn.commit()
    return conn


def test_scope_resolves_parent_to_enabled_descendants(tmp_path):
    conn = _db(tmp_path)
    assert resolve_category_scope(conn, ["cat-03"]) == ["company_child", "company_standards"]
    conn.close()


def test_all_scope_excludes_disabled_categories(tmp_path):
    conn = _db(tmp_path)
    conn.execute(
        "UPDATE category_nodes SET chat_search_enabled=0,chat_filter_selectable=0 WHERE id='cat-99'"
    )
    conn.commit()
    keys = resolve_category_scope(conn, None)
    assert "pending_confirmation" not in keys
    assert "company_standards" in keys
    conn.close()


def test_unavailable_or_non_selectable_scope_is_rejected(tmp_path):
    conn = _db(tmp_path)
    conn.execute(
        "UPDATE category_nodes SET chat_filter_selectable=0 WHERE id='cat-03'"
    )
    conn.commit()
    with pytest.raises(ValueError, match="knowledge_scope_not_selectable"):
        resolve_category_scope(conn, ["cat-03"])
    conn.close()


def test_legacy_category_name_is_resolved(tmp_path):
    conn = _db(tmp_path)
    assert resolve_category_scope(conn, ["公司内部标准"]) == [
        "company_child",
        "company_standards",
    ]
    conn.close()


def test_scope_listing_preserves_tree_metadata(tmp_path):
    conn = _db(tmp_path)
    scopes = list_knowledge_scopes(conn)
    company = next(scope for scope in scopes if scope["id"] == "cat-03")
    assert company["full_path"] == "03 公司内部标准"
    assert company["descendant_count"] == 1
    assert company["chat_filter_selectable"] is True
    conn.close()


def test_scope_listing_hides_categories_not_available_as_filters(tmp_path):
    conn = _db(tmp_path)
    conn.execute(
        "UPDATE category_nodes SET chat_filter_selectable=0 WHERE id='cat-03'"
    )
    conn.commit()
    scope_ids = {scope["id"] for scope in list_knowledge_scopes(conn)}
    assert "cat-03" not in scope_ids
    assert "cat-child" not in scope_ids
    conn.close()


@pytest.mark.parametrize("parent_column", ["is_active", "chat_search_enabled"])
def test_disabled_parent_excludes_enabled_descendants_from_all_scope(tmp_path, parent_column):
    conn = _db(tmp_path)
    conn.execute(f"UPDATE category_nodes SET {parent_column}=0 WHERE id='cat-03'")
    conn.commit()

    assert "company_child" not in resolve_category_scope(conn, None)
    with pytest.raises(ValueError, match="knowledge_scope_unavailable"):
        resolve_category_scope(conn, ["cat-child"])
    assert "cat-child" not in {scope["id"] for scope in list_knowledge_scopes(conn)}

    conn.execute(f"UPDATE category_nodes SET {parent_column}=1 WHERE id='cat-03'")
    conn.commit()
    assert "company_child" in resolve_category_scope(conn, None)
    conn.close()


def test_disabled_child_setting_survives_parent_reenable(tmp_path):
    conn = _db(tmp_path)
    conn.execute("UPDATE category_nodes SET chat_search_enabled=0 WHERE id='cat-child'")
    conn.execute("UPDATE category_nodes SET chat_search_enabled=0 WHERE id='cat-03'")
    conn.execute("UPDATE category_nodes SET chat_search_enabled=1 WHERE id='cat-03'")
    conn.commit()
    assert "company_child" not in resolve_category_scope(conn, None)
    conn.close()


def test_empty_resolved_scope_never_runs_unfiltered_retrieval(monkeypatch):
    def unexpected_retrieve(*_args, **_kwargs):
        raise AssertionError("empty knowledge scope must not call retrieve")

    monkeypatch.setattr(session_module, "retrieve", unexpected_retrieve)
    session = session_module.ChatSession()
    assert session._fresh_retrieve("测试问题", []) == []
