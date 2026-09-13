"""OpenAI 兼容 LLM 客户端基类（httpx 异步实现）。

不依赖 openai SDK，直接打 /chat/completions，便于控制流式输出与成本统计。
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Optional

import httpx

from app.utils.logging import get_logger

logger = get_logger(__name__)


class LLMError(Exception):
    """LLM 调用异常。"""


class ChatMessage(dict):
    """便捷构造消息字典。"""

    @classmethod
    def system(cls, content: str) -> "ChatMessage":
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> "ChatMessage":
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str) -> "ChatMessage":
        return cls(role="assistant", content=content)


class LLMClient(ABC):
    """OpenAI 兼容接口的抽象基类。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.extra_body: dict[str, Any] = {}

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    async def tool_turn(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """One native tool-call turn; preserve assistant tool IDs and completion status."""
        if not self.api_key:
            raise LLMError("未配置 API Key，无法执行真实模型实验")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self._chat_url(), headers=self._headers(), json={
                "model": self.model, "messages": messages, "tools": tools,
                "temperature": 0, "max_tokens": self.max_tokens, "stream": False, **self.extra_body,
            })
            response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]
        return {"message": choice["message"], "finish_reason": choice["finish_reason"],
                "usage": data.get("usage", {})}

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict[str, Any]] = None,
    ) -> str:
        """非流式补全，返回文本。"""
        if not self.api_key:
            raise LLMError("未配置 API Key，无法调用 LLM")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        payload.update(self.extra_body)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self._chat_url(), headers=self._headers(), json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500]
            logger.error("LLM HTTP %s: %s", exc.response.status_code, body)
            raise LLMError(f"LLM 请求失败 {exc.response.status_code}: {body}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM 网络错误: {exc}") from exc

        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"LLM 响应格式异常: {data}") from exc

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Any:
        """请求 JSON 输出并解析（兼容不支持 response_format 的服务）。"""
        text = await self.complete(messages, temperature=temperature, max_tokens=max_tokens)
        return self._extract_json(text)

    async def stream(self, messages: list[dict[str, str]], **kwargs: Any) -> AsyncIterator[str]:
        """SSE 流式补全，逐段 yield 增量文本。"""
        if not self.api_key:
            raise LLMError("未配置 API Key，无法调用 LLM")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        payload.update(kwargs)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST", self._chat_url(), headers=self._headers(), json=payload
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[len("data:") :].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk["choices"][0]["delta"].get("content")
                            if delta:
                                yield delta
                        except (KeyError, IndexError, json.JSONDecodeError):
                            continue
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM 流式请求失败: {exc}") from exc

    @staticmethod
    def _extract_json(text: str) -> Any:
        """从文本中提取 JSON（容忍模型输出多余说明/代码块）。"""
        text = text.strip()
        # 去除 markdown 代码块围栏
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        start = text.find("{")
        start_arr = text.find("[")
        if start == -1 or (0 <= start_arr < start):
            start = start_arr
        end = text.rfind("}")
        end_arr = text.rfind("]")
        end = max(end, end_arr)
        if start == -1 or end == -1 or end <= start:
            raise LLMError(f"无法从模型输出中解析 JSON: {text[:200]}")
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"JSON 解析失败: {text[:200]}") from exc

    @abstractmethod
    def name(self) -> str:
        """客户端标识（用于日志与成本归属）。"""
