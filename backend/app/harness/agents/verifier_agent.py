"""Verifier Agent：答案校验（引用支撑 / 与原文矛盾 / 遗漏 / 格式），不通过打回重写。"""
from __future__ import annotations

from typing import Any

from app.harness.base import Answer, VerificationResult
from app.llm.client import ChatMessage, LLMClient
from app.utils.logging import get_logger
from app.integrations.pi_runtime import PiAgentRuntimeClient

logger = get_logger(__name__)

VERIFY_PROMPT = """你是答案校验助手。检查答案是否可靠，仅输出 JSON：
{{"passed": true/false, "score": 0.0-1.0, "issues": ["问题1", "问题2"]}}

检查项：
1. 每个关键结论是否有条款支撑（引用来源）
2. 是否与原文矛盾
3. 是否遗漏关键信息
4. 引用格式是否正确

参考条款：
{chunks}

答案：
{answer}

用户问题：{query}
"""


class VerifierAgent:
    def __init__(
        self, llm: LLMClient, pi_runtime: PiAgentRuntimeClient | None = None, timeout: float = 3.0,
    ) -> None:
        self.llm = llm
        self.pi_runtime = pi_runtime
        self.timeout = timeout

    async def verify(self, query: str, answer: Answer, chunks: list[dict[str, Any]]) -> VerificationResult:
        if not chunks:
            return VerificationResult(passed=True, score=0.9, issues=[])
        chunks_text = "\n\n".join(f"[{i+1}] {c.get('content', '')[:500]}" for i, c in enumerate(chunks[:8]))
        try:
            prompt = VERIFY_PROMPT.format(chunks=chunks_text, answer=answer.content, query=query)
            data = None
            if self.pi_runtime is not None:
                data = await self.pi_runtime.run_json(
                    "verify", "你是严谨的答案校验助手。", prompt,
                    timeout_seconds=self.timeout,
                )
            if not isinstance(data, dict):
                messages = [ChatMessage.system("你是严谨的答案校验助手。"), ChatMessage.user(prompt)]
                data = await self.llm.complete_json(messages, temperature=0.0)
            return VerificationResult.from_dict(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("答案校验 LLM 失败(%s)，使用启发式校验", exc)
            return self._heuristic(answer, chunks)

    @staticmethod
    def _heuristic(answer: Answer, chunks: list[dict[str, Any]]) -> VerificationResult:
        issues: list[str] = []
        if not answer.citations:
            issues.append("答案缺少引用来源")
        if not answer.content.strip():
            issues.append("答案为空")
        score = 1.0 - 0.3 * len(issues)
        return VerificationResult(passed=len(issues) == 0, score=max(score, 0.0), issues=issues)
