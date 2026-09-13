"""鉴权接口：登录 / 当前用户 / 用户列表。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import require_admin, require_user
from app.api.schemas import ApiResponse, LoginRequest
from app.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=ApiResponse)
async def login(body: LoginRequest, request: Request):
    container = request.app.state.container
    # 登录限流：按 客户端IP+用户名 计数（单进程内存版）
    client_ip = request.client.host if request.client else "unknown"
    limit_key = f"{client_ip}:{body.username.strip().lower()}"
    if not container.login_limiter.check(limit_key):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")
    user = await container.auth.authenticate(body.username, body.password)
    if user is None:
        container.login_limiter.hit(limit_key)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    container.login_limiter.clear(limit_key)
    token = container.auth.issue_token(user["id"])
    return ApiResponse(data={"token": token, "user": user})


@router.get("/me", response_model=ApiResponse)
async def me(user: dict = Depends(require_user)):
    return ApiResponse(data=user)


@router.get("/users", response_model=ApiResponse)
async def list_users(request: Request, _: dict = Depends(require_admin)):
    container = request.app.state.container
    return ApiResponse(data=await container.auth.list_users())
