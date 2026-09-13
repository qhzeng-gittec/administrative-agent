"""pi 智能体服务 HTTP 客户端：Python 后端将 Harness/Loop 推理委托给 pi（回退本地）。

pi 服务地址由 `PI_AGENT_URL` 配置；调用失败时由 Orchestrator 回退到本地 Agent。
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from app.config import Settings
from app.utils.logging import get_logger

logger = get_logger(__name__)


class PiAgentClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.pi_agent_url.rstrip("/")
        self.timeout = settings.pi_agent_timeout

    @property
    def enabled(self) -> bool:
        return self.settings.pi_agent_enabled and bool(self.base_url)

    async def answer(
        self,
        query: str,
        session_id: str,
        user_id: str,
        dept_ids: Optional[list[str]] = None,
    ) -> dict[str, Any] | None:
        """调用 pi 服务 /answer。返回结果 dict，失败返回 None。"""
        if not self.enabled:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/answer",
                    headers={"X-Internal-Token": self.settings.internal_api_token},
                    json={"query": query, "sessionId": session_id, "userId": user_id, "deptIds": dept_ids},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.warning("pi-agent /answer 调用失败: %s", exc)
            return None
        if data.get("code") != 0:
            logger.warning("pi-agent /answer 返回错误: %s", data.get("message"))
            return None
        return data.get("data")

    async def run_loop(self) -> dict[str, Any] | None:
        """调用 pi 服务 /loop/run。失败返回 None。"""
        if not self.enabled:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/loop/run", json={},
                    headers={"X-Internal-Token": self.settings.internal_api_token},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.warning("pi-agent /loop/run 调用失败: %s", exc)
            return None
        if data.get("code") != 0:
            return None
        return data.get("data")

    async def health(self) -> bool:
        """探活。"""
        if not self.enabled:
            return False
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.base_url}/health")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False
