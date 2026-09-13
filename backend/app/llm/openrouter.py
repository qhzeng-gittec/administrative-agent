"""OpenRouter transport with explicit completion checks and optional experiment audit."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from app.llm.client import LLMClient, LLMError


class OpenRouterClient(LLMClient):
    def __init__(self, settings, model: str, audit: Path | None = None):
        super().__init__(settings.openrouter_base_url, settings.openrouter_api_key, model,
                         timeout=120, max_tokens=6000, temperature=0)
        self.audit = audit
        self.extra_body = {"reasoning": {"enabled": False}}

    def name(self) -> str:
        return "openrouter"

    async def complete_json(self, messages, *, temperature=None, max_tokens=None):
        text = await self.complete(messages, temperature=temperature, max_tokens=max_tokens,
                                   response_format={"type": "json_object"})
        return json.loads(text)

    async def request(self, endpoint: str, payload: dict) -> dict:
        if not self.api_key:
            raise LLMError("请配置 OPENROUTER_API_KEY；不会使用模拟模型替代")
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(f"{self.base_url}/{endpoint}", headers=self._headers(), json=payload)
                    response.raise_for_status()
                break
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                transient = isinstance(exc, httpx.TransportError) or exc.response.status_code in {429, 500, 502, 503, 504}
                if not transient or attempt == 2:
                    raise
                if self.audit:
                    self.audit.parent.mkdir(parents=True, exist_ok=True)
                    with self.audit.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps({"endpoint": endpoint, "retry": attempt+1, "error": type(exc).__name__}, ensure_ascii=False) + "\n")
                await asyncio.sleep(attempt+1)
        data = response.json()
        if self.audit:
            self.audit.parent.mkdir(parents=True, exist_ok=True)
            with self.audit.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"endpoint": endpoint, "request": payload, "response": data}, ensure_ascii=False) + "\n")
        if "error" in data:
            raise LLMError(f"OpenRouter 返回错误：{data['error']}")
        return data

    async def complete(self, messages, *, temperature=None, max_tokens=None, response_format=None):
        data = await self.request("chat/completions", {
            "model": self.model, "messages": messages, "stream": False,
            "temperature": 0 if temperature is None else temperature,
            "max_tokens": max_tokens or self.max_tokens,
            **({"response_format": response_format} if response_format else {}), **self.extra_body,
        })
        choice = data["choices"][0]
        if choice["finish_reason"] != "stop" or not choice["message"].get("content"):
            raise LLMError(f"模型未完成结构化输出：{choice['finish_reason']}")
        return choice["message"]["content"]

    async def tool_turn(self, messages, tools):
        data = await self.request("chat/completions", {
            "model": self.model, "messages": messages, "tools": tools, "tool_choice": "required", "stream": False,
            "temperature": 0, "max_tokens": self.max_tokens, **self.extra_body,
        })
        choice = data["choices"][0]
        return {"message": choice["message"], "finish_reason": choice["finish_reason"], "usage": data.get("usage", {})}

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        data = await self.request("embeddings", {"model": model, "input": texts})
        rows = sorted(data["data"], key=lambda r: r["index"])
        if [r["index"] for r in rows] != list(range(len(texts))):
            raise LLMError("Embedding 返回的条数或顺序不完整")
        return [r["embedding"] for r in rows]
