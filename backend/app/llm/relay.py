"""中转站客户端（OpenAI 兼容）。

用于 bge 等非 DeepSeek 模型，例如：
    base_url=https://yunwu.ai/v1，api_key=中转站 key。

内置对 Embedding / Rerank 的重试与超时控制，中转站网络抖动时自动重试，
最终失败由上层（EmbeddingClient / Reranker）降级到 hash / 启发式。
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from app.config import Settings
from app.llm.client import LLMClient, LLMError
from app.utils.logging import get_logger

logger = get_logger(__name__)


class RelayClient(LLMClient):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            base_url=settings.relay_base_url,
            api_key=settings.relay_api_key,
            model=settings.relay_model,
            timeout=settings.deepseek_timeout,
            temperature=settings.deepseek_temperature,
            max_tokens=settings.deepseek_max_tokens,
        )

    def name(self) -> str:
        return "relay"

    async def _post_json(self, url: str, payload: dict[str, Any], retries: int = 2) -> dict[str, Any]:
        """带重试的 POST：4xx（认证/模型名错误）不重试，网络错误/5xx 重试。"""
        last_exc: Optional[Exception] = None
        for attempt in range(retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, headers=self._headers(), json=payload)
                    resp.raise_for_status()
                    return resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise LLMError(f"请求失败 {exc.response.status_code}: {exc.response.text[:300]}") from exc
                last_exc = exc
            except httpx.HTTPError as exc:
                last_exc = exc
            if attempt < retries - 1:
                await asyncio.sleep(1.0)
        raise LLMError(f"网络错误(重试 {retries} 次后失败): {last_exc}") from last_exc

    async def embed(self, texts: list[str], model: Optional[str] = None) -> list[list[float]]:
        """调用中转站 /embeddings 接口生成向量。"""
        if not self.api_key:
            raise LLMError("未配置中转站 API Key，无法调用 Embedding")
        payload: dict[str, Any] = {"model": model or "text-embedding-3-large", "input": texts}
        data = await self._post_json(f"{self.base_url}/embeddings", payload)
        try:
            items = sorted(data["data"], key=lambda d: d.get("index", 0))
            return [item["embedding"] for item in items]
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Embedding 响应格式异常: {str(data)[:300]}") from exc

    async def rerank(self, query: str, documents: list[str], model: Optional[str] = None) -> list[float]:
        """调用中转站 /rerank 接口（bge-reranker-v2-m3 等），返回相关性得分列表。"""
        if not self.api_key:
            raise LLMError("未配置中转站 API Key，无法调用 Rerank")
        payload: dict[str, Any] = {
            "model": model or "BAAI/bge-reranker-v2-m3",
            "query": query,
            "documents": documents,
        }
        data = await self._post_json(f"{self.base_url}/rerank", payload)
        try:
            return [float(r.get("relevance_score", 0.0)) for r in data["results"]]
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Rerank 响应格式异常: {str(data)[:300]}") from exc
