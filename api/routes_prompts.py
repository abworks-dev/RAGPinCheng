"""Admin API for system prompt management.

Reads and edits the persistent ``system_prompts`` table used by the runtime
prompt override layer (``src.prompts``).  Only system administrators may
manage prompts; mutating endpoints require CSRF.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .auth import CurrentUser, require_admin, require_csrf_admin
from .prompt_store import list_prompts, restore_prompt, update_prompt
from src.prompts import clear_prompt_override_cache

router = APIRouter(prefix="/admin/prompts", tags=["admin-prompts"])

_PROMPT_BODY_LIMIT = 20_000


class PromptItemDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    title: str
    description: str
    default_body: str
    custom_body: str | None
    updated_by: int | None
    updated_at: int | None


class PromptUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    custom_body: str = Field(max_length=_PROMPT_BODY_LIMIT)


@router.get("", response_model=list[PromptItemDTO])
def get_prompts(_admin: CurrentUser = Depends(require_admin)):
    return [PromptItemDTO(**item) for item in list_prompts()]


@router.put("/{key}", response_model=PromptItemDTO)
def put_prompt(key: str, body: PromptUpdateRequest, admin: CurrentUser = Depends(require_csrf_admin)):
    try:
        update_prompt(key, custom_body=body.custom_body, updated_by=admin.id)
        clear_prompt_override_cache()
    except KeyError:
        raise HTTPException(status_code=404, detail="提示词不存在")
    except Exception:
        raise HTTPException(status_code=409, detail="保存提示词失败，请稍后重试")
    item = next((entry for entry in list_prompts() if entry["key"] == key), None)
    if item is None:
        raise HTTPException(status_code=404, detail="提示词不存在")
    return PromptItemDTO(**item)


@router.post("/{key}/restore", response_model=PromptItemDTO)
def post_restore_prompt(key: str, admin: CurrentUser = Depends(require_csrf_admin)):
    try:
        restore_prompt(key, updated_by=admin.id)
        clear_prompt_override_cache()
    except KeyError:
        raise HTTPException(status_code=404, detail="提示词不存在")
    except Exception:
        raise HTTPException(status_code=409, detail="恢复默认失败，请稍后重试")
    item = next((entry for entry in list_prompts() if entry["key"] == key), None)
    if item is None:
        raise HTTPException(status_code=404, detail="提示词不存在")
    return PromptItemDTO(**item)