"""Bearer authentication without secret reflection."""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException


def require_bearer(expected_token: str):
    async def dependency(authorization: str | None = Header(default=None)) -> None:
        prefix = "Bearer "
        supplied = (
            authorization[len(prefix) :]
            if authorization is not None and authorization.startswith(prefix)
            else ""
        )
        if not expected_token or not supplied or not hmac.compare_digest(supplied, expected_token):
            raise HTTPException(
                status_code=401, detail={"code": "authentication_failed"}
            )

    return dependency
