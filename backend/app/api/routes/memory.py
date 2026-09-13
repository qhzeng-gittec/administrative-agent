"""记忆治理 API：用户自有记忆、组织记忆与会话摘要。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import require_admin, require_user, scope_dept
from app.api.schemas import ApiResponse, OrganizationMemoryCreate, UserMemoryCreate

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/me", response_model=ApiResponse)
async def my_memories(request: Request, user: dict = Depends(require_user)):
    rows = await request.app.state.container.user_semantic_memory.recall(user["id"], limit=100)
    return ApiResponse(data=rows)


@router.post("/me", response_model=ApiResponse)
async def remember_for_me(body: UserMemoryCreate, request: Request, user: dict = Depends(require_user)):
    if not body.consent:
        raise HTTPException(status_code=400, detail="用户长期记忆需要明确同意")
    try:
        item = await request.app.state.container.user_semantic_memory.remember(
            user["id"], body.key, body.value, body.category, source_type="explicit_user",
            consent=True, actor_id=user["id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(data=item)


@router.delete("/me/{memory_id}", response_model=ApiResponse)
async def forget_for_me(memory_id: str, request: Request, user: dict = Depends(require_user)):
    deleted = await request.app.state.container.user_semantic_memory.forget(
        user["id"], memory_id, user["id"]
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return ApiResponse(data={"deleted": memory_id})


@router.get("/sessions/{session_id}/summary", response_model=ApiResponse)
async def session_summary(session_id: str, request: Request, user: dict = Depends(require_user)):
    summary = await request.app.state.container.episodic_memory.get_summary(session_id, user["id"])
    if not summary:
        raise HTTPException(status_code=404, detail="会话摘要不存在")
    return ApiResponse(data=summary)


@router.get("/organization", response_model=ApiResponse)
async def organization_memories(request: Request, user: dict = Depends(require_admin)):
    scope = scope_dept(user)
    rows = await request.app.state.container.store.find("org_memory_items")
    if scope:
        rows = [row for row in rows if row.get("scope") == "global" or row.get("dept_id") == scope]
    return ApiResponse(data=rows)


@router.post("/organization", response_model=ApiResponse)
async def publish_organization_memory(
    body: OrganizationMemoryCreate, request: Request, user: dict = Depends(require_admin)
):
    admin_scope = scope_dept(user)
    dept_id = admin_scope or body.dept_id
    if admin_scope and body.scope == "global":
        raise HTTPException(status_code=403, detail="部门管理员不能发布全局记忆")
    if body.scope == "department" and not dept_id:
        raise HTTPException(status_code=400, detail="部门记忆必须指定 dept_id")
    try:
        item = await request.app.state.container.organization_memory.publish(
            body.scope, body.type, body.title, body.content,
            [ref.model_dump() for ref in body.source_refs], dept_id=dept_id,
            authority="admin_approved", review_status="approved", access_scope=body.access_scope,
            effective_from=body.effective_from, effective_to=body.effective_to, actor_id=user["id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(data=item)


@router.delete("/organization/{memory_id}", response_model=ApiResponse)
async def archive_organization_memory(
    memory_id: str, request: Request, user: dict = Depends(require_admin)
):
    store = request.app.state.container.store
    item = await store.get("org_memory_items", memory_id)
    if not item:
        raise HTTPException(status_code=404, detail="组织记忆不存在")
    admin_scope = scope_dept(user)
    if admin_scope and item.get("dept_id") != admin_scope:
        raise HTTPException(status_code=403, detail="无权撤销其它部门记忆")
    item.update({"status": "archived", "updated_at": datetime.now(timezone.utc)})
    await store.upsert("org_memory_items", item)
    return ApiResponse(data={"archived": memory_id})
