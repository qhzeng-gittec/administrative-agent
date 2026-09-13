"""Prometheus 指标：问答时延、检索命中率、采纳率、成本等。"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

QUERY_TOTAL = Counter("wenshu_query_total", "总问答次数", ["dept", "intent"])
QUERY_LATENCY = Histogram("wenshu_query_latency_seconds", "端到端问答时延", ["dept"])
LLM_LATENCY = Histogram("wenshu_llm_latency_seconds", "LLM 调用时延", ["model"])
RETRIEVAL_HIT = Counter("wenshu_retrieval_hit_total", "检索命中次数", ["dept"])
RETRIEVAL_MISS = Counter("wenshu_retrieval_miss_total", "检索未命中次数", ["dept"])
ADOPTION = Counter("wenshu_answer_adoption_total", "回答采纳/点踩次数", ["kind"])  # kind: up/down
LLM_COST = Gauge("wenshu_llm_cost_yuan", "累计 LLM 成本（元）")
FEEDBACK_TOTAL = Counter("wenshu_feedback_total", "反馈总数", ["kind"])
SKILL_TRIGGER = Counter("wenshu_skill_trigger_total", "Skill 触发次数", ["skill"])
DEPT_AGENT_REQUEST = Counter("wenshu_dept_agent_requests_total", "部门 Agent 请求数", ["dept", "status"])
DEPT_AGENT_INFLIGHT = Gauge("wenshu_dept_agent_inflight", "部门 Agent 当前在途请求", ["dept"])
PI_AGENT_EXECUTION = Counter("wenshu_pi_agent_execution_total", "pi Agent 执行次数", ["agent", "status"])


def record_retrieval_hit(dept: str, hit: bool) -> None:
    (RETRIEVAL_HIT if hit else RETRIEVAL_MISS).labels(dept=dept).inc()


def record_adoption(kind: str) -> None:
    ADOPTION.labels(kind=kind).inc()
