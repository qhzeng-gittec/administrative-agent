"""Query Rewriter：补全省略信息、术语标准化（查 glossary）、生成多组检索 query。"""
from __future__ import annotations
from typing import Optional

from app.harness.base import Intent
from app.llm.client import ChatMessage, LLMClient
from app.storage.store import DataStore
from app.utils.logging import get_logger
from app.integrations.pi_runtime import PiAgentRuntimeClient

logger = get_logger(__name__)

REWRITE_PROMPT = """你是检索查询改写助手。将用户问题改写为 1-3 个更适合检索的 query（补全省略、规范化术语）。

术语表：
{glossary}

仅输出 JSON：
{{"queries": ["query1", "query2"]}}

用户问题：{query}
"""


class QueryRewriter:
    def __init__(
        self, llm: LLMClient, store: DataStore, pi_runtime: PiAgentRuntimeClient | None = None,
        timeout: float = 2.0,
    ) -> None:
        self.llm = llm
        self.store = store
        self.pi_runtime = pi_runtime
        self.timeout = timeout

    async def rewrite(self, query: str, intent: Optional[Intent] = None, memory_context: str = "") -> list[str]:
        glossary = await self.store.list_glossary()
        gloss_text = "; ".join(f"{g['canonical']}≈{'/'.join(g.get('synonyms', []))}" for g in glossary[:50])
        try:
            prompt = REWRITE_PROMPT.format(glossary=gloss_text, query=query) + (
                f"\n对话记忆：{memory_context[:1600]}" if memory_context else ""
            )
            data = None
            if self.pi_runtime is not None:
                data = await self.pi_runtime.run_json(
                    "rewrite", "你是检索查询改写助手。", prompt,
                    timeout_seconds=self.timeout,
                )
            if not isinstance(data, dict):
                messages = [ChatMessage.system("你是检索查询改写助手。"), ChatMessage.user(prompt)]
                data = await self.llm.complete_json(messages, temperature=0.2)
            queries = data.get("queries") if isinstance(data, dict) else None
            if isinstance(queries, list):
                return self._dedupe([q for q in queries if isinstance(q, str) and q.strip()])
        except Exception as exc:  # noqa: BLE001
            logger.warning("查询改写失败(%s)，使用原 query + 术语扩展", exc)
        return self._glossary_expand(query, glossary)

    def _glossary_expand(self, query: str, glossary: list[dict]) -> list[str]:
        """术语表同义词扩展（无 LLM 回退）。"""
        queries = [query]
        expanded = query
        for g in glossary:
            canonical = g.get("canonical", "")
            if canonical and canonical in query:
                for syn in g.get("synonyms", []):
                    if syn and syn not in expanded:
                        expanded += f" {syn}"
        if expanded != query:
            queries.append(expanded)
        return self._dedupe(queries)

    @staticmethod
    def _dedupe(queries: list[str]) -> list[str]:
        seen: list[str] = []
        for q in queries:
            if q and q not in seen:
                seen.append(q)
        return seen or ["*"]
