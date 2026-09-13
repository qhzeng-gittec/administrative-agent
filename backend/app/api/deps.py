"""鉴权依赖：从 Authorization: Bearer <token> 解析当前用户。"""
from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Header, HTTPException, Request


async def get_optional_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> Optional[dict]:
    """解析可选的当前用户（未登录返回 None，不报错）。"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer "):].strip()
    auth = request.app.state.container.auth
    user_id = auth.verify_token(token)
    if not user_id:
        return None
    return await auth.get_user(user_id)


async def require_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict:
    user = await get_optional_user(request, authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return user


async def require_admin(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict:
    user = await require_user(request, authorization)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


async def require_internal(
    request: Request,
    x_internal_token: Optional[str] = Header(default=None, alias="X-Internal-Token"),
) -> None:
    """内部接口鉴权：校验 X-Internal-Token 与配置一致。

    fail-closed：未配置 INTERNAL_API_TOKEN 时直接拒绝（503），避免默认开放。
    """
    token = request.app.state.container.settings.internal_api_token
    if not token:
        raise HTTPException(status_code=503, detail="内部接口未配置访问令牌（INTERNAL_API_TOKEN）")
    if not x_internal_token or not hmac.compare_digest(x_internal_token.strip(), token):
        raise HTTPException(status_code=401, detail="内部接口令牌无效")


def scope_dept(user: Optional[dict]) -> Optional[str]:
    """管理员可见的部门范围：dept_id 非空则仅本部门；为空表示系统管理员（全部）。"""
    return (user or {}).get("dept_id") or None
