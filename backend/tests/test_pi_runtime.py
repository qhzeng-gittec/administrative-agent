"""Python 控制平面到 pi Agent Runtime 的协议、优先执行与降级测试。"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.harness.agents.answer_agent import AnswerAgent
from app.harness.agents.intent_agent import IntentAgent
from app.harness.agents.query_rewriter import QueryRewriter
from app.harness.agents.verifier_agent import VerifierAgent
from app.harness.base import Answer, Citation
from app.integrations.pi_runtime import PiAgentRuntimeClient
from app.storage.store import MemoryStore


class FakeLLM:
    def __init__(self, json_result=None, text_result="local"):
        self.json_result = json_result or {}
        self.text_result = text_result
        self.calls = 0

    async def complete_json(self, *args, **kwargs):
        self.calls += 1
        return self.json_result

    async def complete(self, *args, **kwargs):
        self.calls += 1
        return self.text_result


class FakePi:
    def __init__(self, json_result=None, text_result=None):
        self.json_result = json_result
        self.text_result = text_result
        self.calls = []

    async def run_json(self, agent_type, system_prompt, prompt, **kwargs):
        self.calls.append((agent_type, kwargs))
        return self.json_result

    async def run_text(self, agent_type, system_prompt, prompt, **kwargs):
        self.calls.append((agent_type, kwargs))
        return self.text_result


@pytest.mark.asyncio
async def test_intent_prefers_pi_without_calling_local_llm():
    store = MemoryStore()
    await store.upsert_department({"_id": "dept_jwc", "name": "教务处"})
    local = FakeLLM(json_result={"type": "other"})
    pi = FakePi(json_result={
        "type": "deadline_query", "depts": ["dept_jwc"], "user_role": "student",
        "entities": {"matter": "退课"}, "needs_cross_dept": False, "confidence": 0.9,
    })
    intent = await IntentAgent(local, store, pi, 1.0).infer("退课截止时间", "u1")
    assert intent.type == "deadline_query"
    assert local.calls == 0
    assert pi.calls[0][0] == "intent"


@pytest.mark.asyncio
async def test_rewriter_falls_back_to_local_when_pi_unavailable():
    store = MemoryStore()
    local = FakeLLM(json_result={"queries": ["本地改写"]})
    pi = FakePi(json_result=None)
    queries = await QueryRewriter(local, store, pi, 1.0).rewrite("退课")
    assert queries == ["本地改写"]
    assert local.calls == 1


@pytest.mark.asyncio
async def test_answer_and_verifier_use_pi_outputs():
    store = MemoryStore()
    chunk = {
        "_id": "d1:0", "doc_id": "d1", "dept_id": "dept_jwc",
        "chunk_index": 0, "content": "退课截止第八周。", "section_path": [],
    }
    local = FakeLLM(text_result="local answer", json_result={"passed": False})
    pi_answer = FakePi(text_result="退课截止第八周。[来源1]")
    answer = await AnswerAgent(local, store, pi_answer, 5.0).generate("何时退课", [chunk])
    assert answer.content.startswith("退课截止第八周")
    assert local.calls == 0

    pi_verify = FakePi(json_result={"passed": True, "score": 0.95, "issues": []})
    verdict = await VerifierAgent(local, pi_verify, 3.0).verify("何时退课", answer, [chunk])
    assert verdict.passed and verdict.score == 0.95
    assert local.calls == 0


def test_pi_runtime_requires_enabled_flag_and_internal_token():
    disabled = PiAgentRuntimeClient(Settings(pi_agent_enabled=False, internal_api_token="token"))
    missing_token = PiAgentRuntimeClient(Settings(pi_agent_enabled=True, internal_api_token=""))
    enabled = PiAgentRuntimeClient(Settings(pi_agent_enabled=True, internal_api_token="token"))
    assert not disabled.enabled
    assert not missing_token.enabled
    assert enabled.enabled
