from __future__ import annotations

import sqlite3
from collections.abc import Callable

from fastapi import Depends, HTTPException, status

from .auth import CurrentUser, require_csrf, require_user
from .db import get_db

CONTENT_PERMISSIONS = frozenset(
    {"organize", "review", "publish", "manage_categories", "import_server"}
)


def has_content_permission(
    conn: sqlite3.Connection,
    user: CurrentUser,
    permission: str,
) -> bool:
    if permission not in CONTENT_PERMISSIONS:
        raise ValueError("unknown_content_permission")
    if user.role == "admin":
        return True
    return conn.execute(
        "SELECT 1 FROM content_permissions WHERE user_id=? AND permission=?",
        (user.id, permission),
    ).fetchone() is not None


def require_content_permission(
    permission: str,
    *,
    csrf: bool = False,
) -> Callable[..., CurrentUser]:
    if permission not in CONTENT_PERMISSIONS:
        raise ValueError("unknown_content_permission")
    base_dependency = require_csrf if csrf else require_user

    def dependency(
        user: CurrentUser = Depends(base_dependency),
        conn: sqlite3.Connection = Depends(get_db),
    ) -> CurrentUser:
        if not has_content_permission(conn, user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"content permission required: {permission}",
            )
        return user

    return dependency


def require_any_content_permission(
    permissions: frozenset[str],
    *,
    csrf: bool = False,
) -> Callable[..., CurrentUser]:
    if not permissions or not permissions.issubset(CONTENT_PERMISSIONS):
        raise ValueError("unknown_content_permission")
    base_dependency = require_csrf if csrf else require_user

    def dependency(
        user: CurrentUser = Depends(base_dependency),
        conn: sqlite3.Connection = Depends(get_db),
    ) -> CurrentUser:
        if user.role == "admin":
            return user
        placeholders = ",".join("?" for _ in permissions)
        row = conn.execute(
            f"SELECT 1 FROM content_permissions WHERE user_id=? AND permission IN ({placeholders}) LIMIT 1",
            (user.id, *sorted(permissions)),
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="content permission required",
            )
        return user

    return dependency
