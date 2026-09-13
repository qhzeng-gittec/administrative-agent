"""Answer Agent：基于检索 chunks 生成带引用的答案，遵守 Rules 硬约束。"""
from __future__ import annotations

from typing import Any, Optional

from app.harness.base import Answer, Citation, Intent
from app.llm.client import ChatMessage, LLMClient
from app.utils.logging import get_logger
from app.integrations.pi_runtime import PiAgentRuntimeClient

logger = get_logger(__name__)

ANSWER_PROMPT = """你是学校制度咨询助手"文枢"。请基于给定的制度条款回答用户问题。

【必须遵守的规则】
{rules}

【回答要求】
- 只依据给定条款回答，不得编造；若条款中无明确答案，明确回答"根据现有制度文件未找到明确规定"。
- 每句关键结论后以 [来源1] 形式标注引用编号。
- 使用简洁、准确的中文，必要时分点说明。

【参考条款】
{chunks}

用户问题：{query}
"""


class AnswerAgent:
    def __init__(
        self, llm: LLMClient, store, pi_runtime: PiAgentRuntimeClient | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.llm = llm
        self.store = store
        self.pi_runtime = pi_runtime
        self.timeout = timeout

    async def generate(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        rules: list[dict[str, Any]] | None = None,
        intent: Optional[Intent] = None,
        user_prefs: Optional[dict[str, Any]] = None,
        extra_instructions: str = "",
        memory_context: str = "",
    ) -> Answer:
        rules = rules or []
        rules_text = "\n".join(f"- {r.get('content', '')}" for r in rules) or "- 必须引用来源"
        if extra_instructions:
            rules_text += "\n" + extra_instructions
        if memory_context:
            rules_text += "\n以下记忆仅用于理解上下文和回答风格，不能作为制度事实或引用来源：\n" + memory_context[:2400]
        if not chunks:
            return Answer(content="根据现有制度文件未找到明确规定，建议咨询相关部门。", citations=[], dept_ids=[])

        chunks_text, citations = self._format_chunks(chunks)
        try:
            prompt = ANSWER_PROMPT.format(rules=rules_text, chunks=chunks_text, query=query)
            content = None
            if self.pi_runtime is not None:
                content = await self.pi_runtime.run_text(
                    "answer", "你是学校制度咨询助手，回答严谨、有据可依。", prompt,
                    allowed_tools=[], timeout_seconds=self.timeout,
                )
            if not content:
                messages = [
                    ChatMessage.system("你是学校制度咨询助手，回答严谨、有据可依。"),
                    ChatMessage.user(prompt),
                ]
                content = await self.llm.complete(messages, temperature=0.2)
        except Exception as exc:  # noqa: BLE001
            logger.warning("答案生成 LLM 失败(%s)，回退原文拼接", exc)
            content = self._fallback_answer(query, chunks)

        dept_ids = sorted({c["dept_id"] for c in chunks if c.get("dept_id")})
        return Answer(content=content, citations=citations, dept_ids=dept_ids, confidence=0.7)

    def _format_chunks(self, chunks: list[dict[str, Any]]) -> tuple[str, list[Citation]]:
        lines: list[str] = []
        citations: list[Citation] = []
        for i, c in enumerate(chunks, start=1):
            lines.append(f"[来源{i}] {c.get('content', '')}")
            citations.append(
                Citation(
                    doc_id=c.get("doc_id", ""),
                    doc_title=c.get("doc_title", c.get("section_title", "")),
                    dept_id=c.get("dept_id", ""),
                    chunk_index=c.get("chunk_index", 0),
                    section_path=c.get("section_path", []),
                    snippet=c.get("content", "")[:200],
                )
            )
        return "\n\n".join(lines), citations

    @staticmethod
    def _fallback_answer(query: str, chunks: list[dict[str, Any]]) -> str:
        parts = ["根据检索到的制度条款："]
        for i, c in enumerate(chunks[:3], start=1):
            parts.append(f"[来源{i}] {c.get('content', '')[:300]}")
        return "\n".join(parts)
