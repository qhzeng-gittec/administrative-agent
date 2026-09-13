"""测试 Agent 的无 LLM 回退逻辑。"""
from __future__ import annotations

from app.harness.base import Answer, VerificationResult
from app.harness.agents.intent_agent import IntentAgent
from app.harness.agents.query_rewriter import QueryRewriter
from app.harness.agents.verifier_agent import VerifierAgent


class _FakeLLM:
    """模拟 LLM，complete 抛错以测试回退。"""

    def __init__(self):
        self.calls = 0

    async def complete(self, *a, **kw):
        self.calls += 1
        raise RuntimeError("no llm")

    async def complete_json(self, *a, **kw):
        self.calls += 1
        raise RuntimeError("no llm")


def test_intent_fallback():
    store = _MemStore()
    agent = IntentAgent(_FakeLLM(), store)
    import asyncio

    intent = asyncio.run(agent.infer("退课截止时间是第几周？", "u1", "上一轮事项：退课"))
    assert intent.type == "deadline_query"
    assert "dept_jwc" in intent.depts


def test_query_rewriter_fallback():
    store = _MemStore()
    rw = QueryRewriter(_FakeLLM(), store)
    import asyncio

    queries = asyncio.run(rw.rewrite("退课怎么办", None))
    assert queries and queries[0] == "退课怎么办"


def test_verifier_heuristic():
    v = VerifierAgent(_FakeLLM())
    import asyncio

    answer = Answer(content="根据规定应于第8周前退课", citations=[])
    result = asyncio.run(v.verify("退课时间", answer, []))
    assert isinstance(result, VerificationResult)


class _MemStore:
    async def list_departments(self):
        return [{"_id": "dept_jwc", "name": "教务处"}, {"_id": "dept_cwc", "name": "财务处"}]

    async def get_user_profile(self, user_id):
        return None

    async def list_glossary(self):
        return []
