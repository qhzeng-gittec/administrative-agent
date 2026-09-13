"""Orchestrator：总调度，编排多智能体 DAG 完成一次问答。

流程：Skills/Hooks/Rules 加载 → FAQ 缓存 → Intent → Hook 扩展 → Rewrite → Retrieve
      → Answer → Verify（最多 2 次打回）→ 返回答案 + 引用 + trace。
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

from app.config import Settings
from app.harness.agents.answer_agent import AnswerAgent
from app.harness.agents.dept_router import DeptRouter
from app.harness.agents.feedback_agent import FeedbackAgent
from app.harness.agents.intent_agent import IntentAgent
from app.harness.agents.query_rewriter import QueryRewriter
from app.harness.agents.retrieval_agent import RetrievalAgent
from app.harness.agents.verifier_agent import VerifierAgent
from app.harness.base import Answer, Citation
from app.integrations.pi_client import PiAgentClient
from app.integrations.dept_agent_client import DepartmentAgentClient
from app.loop.hook_engine import HookEngine
from app.loop.loop_engine import LoopEngine
from app.loop.rule_engine import RuleEngine
from app.loop.skill_executor import SkillExecutor
from app.memory.department import DepartmentMemory
from app.memory.user import UserMemory
from app.memory.working import WorkingMemory
from app.memory.episodic import EpisodicMemory
from app.memory.organization import OrganizationMemory
from app.memory.context_builder import MemoryContext, MemoryContextBuilder
from app.storage.store import DataStore
from app.utils.logging import get_logger
from app.utils import metrics

logger = get_logger(__name__)

MAX_VERIFY_RETRY = 2


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        store: DataStore,
        working_memory: WorkingMemory,
        user_memory: UserMemory,
        dept_memory: DepartmentMemory,
        episodic_memory: EpisodicMemory,
        memory_context_builder: MemoryContextBuilder,
        organization_memory: OrganizationMemory,
        intent_agent: IntentAgent,
        dept_router: DeptRouter,
        query_rewriter: QueryRewriter,
        retrieval_agent: RetrievalAgent,
        answer_agent: AnswerAgent,
        verifier_agent: VerifierAgent,
        feedback_agent: FeedbackAgent,
        loop_engine: LoopEngine,
        hook_engine: HookEngine,
        rule_engine: RuleEngine,
        skill_executor: SkillExecutor,
        dept_agent_client: Optional[DepartmentAgentClient] = None,
        pi_client: Optional[PiAgentClient] = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.working_memory = working_memory
        self.user_memory = user_memory
        self.dept_memory = dept_memory
        self.episodic_memory = episodic_memory
        self.memory_context_builder = memory_context_builder
        self.organization_memory = organization_memory
        self.intent_agent = intent_agent
        self.dept_router = dept_router
        self.query_rewriter = query_rewriter
        self.retrieval_agent = retrieval_agent
        self.answer_agent = answer_agent
        self.verifier_agent = verifier_agent
        self.feedback_agent = feedback_agent
        self.loop_engine = loop_engine
        self.hook_engine = hook_engine
        self.rule_engine = rule_engine
        self.skill_executor = skill_executor
        self.dept_agent_client = dept_agent_client
        self.pi_client = pi_client

    async def answer(
        self,
        query: str,
        session_id: str = "",
        user_id: str = "anonymous",
        dept_ids: Optional[list[str]] = None,
        external_memory_context: str = "",
    ) -> dict[str, Any]:
        session_id = session_id or uuid.uuid4().hex
        started = time.perf_counter()

        # 0. 自动部门路由（用户未指定部门时，由 DeptRouter 匹配最符合的部门）
        route: Optional[dict[str, Any]] = None
        effective_dept_ids = dept_ids
        if self.settings.dept_id:
            if effective_dept_ids and any(d != self.settings.dept_id for d in effective_dept_ids):
                raise PermissionError("部门 Agent 禁止处理其它部门请求")
            effective_dept_ids = [self.settings.dept_id]
        if not effective_dept_ids:
            route = await self.dept_router.route(query)
            effective_dept_ids = route.get("dept_ids") or None

        # 会话/情景记忆：append-only 记录用户输入，并构建第一阶段上下文。
        previous = await self.working_memory.history(session_id)
        if previous and previous[-1].get("role") == "assistant":
            # 同会话继续提问是一种弱负反馈，后续 Loop 会结合显式反馈共同判断。
            await self.loop_engine.feedback_collector.collect_implicit(
                session_id, user_id, query, previous[-1].get("content", ""), "follow_up",
                {"previous_answer": previous[-1].get("content", "")},
            )
        await self.working_memory.append_message(session_id, "user", query)
        await self.episodic_memory.append_event(session_id, user_id, "user_message", query)
        initial_memory = await self.memory_context_builder.build(
            session_id, user_id, query, effective_dept_ids, include_organization=False
        )

        # 1. 加载动态 Hooks；Rules 在部门范围确定后加载。
        hooks = await self.hook_engine.active_hooks()

        # 3. Intent
        initial_prompt = "\n".join(text for text in [external_memory_context, initial_memory.prompt_text()] if text)
        intent = await self.intent_agent.infer(query, user_id, initial_prompt)
        intent.raw["query"] = query
        await self.working_memory.set_intent(session_id, intent.raw)

        # 4. Hook 扩展（跨部门协同）
        resolved_depts = list(effective_dept_ids or intent.depts)
        matched_hooks = self.hook_engine.evaluate(hooks, query, intent)
        resolved_depts = await self.hook_engine.apply(matched_hooks, intent, resolved_depts)
        rules = await self.rule_engine.active_rules(resolved_depts)
        memory_context = await self.memory_context_builder.build(
            session_id, user_id, query, resolved_depts, role=intent.user_role, include_organization=True
        )
        memory_prompt = "\n".join(text for text in [external_memory_context, memory_context.prompt_text()] if text)

        if self.dept_agent_client is not None and self.dept_agent_client.enabled and resolved_depts:
            sub_answers, failed = await self.dept_agent_client.answer_many(
                query, resolved_depts, session_id, user_id, memory_prompt
            )
            if sub_answers:
                degraded: list[str] = []
                if failed:
                    fallback_chunks = await self.retrieval_agent.retrieve([query], failed, top_k=self.settings.hybrid_topk)
                    fallback_rules = await self.rule_engine.active_rules(failed)
                    fallback = await self.answer_agent.generate(query, fallback_chunks, rules=fallback_rules, intent=intent)
                    fallback = await self._enrich_citations(fallback)
                    if fallback.content:
                        sub_answers.append({
                            "answer": fallback.content, "citations": [c.to_dict() for c in fallback.citations],
                            "dept_ids": failed, "confidence": fallback.confidence,
                        })
                        degraded = list(failed)
                        failed = []
                merged = self._merge_department_answers(sub_answers, failed, degraded)
                trace_id = await self._finalize(session_id, user_id, query, merged, intent.type, [], started)
                await self.memory_context_builder.record_usage(memory_context, session_id, trace_id, user_id)
                return self._to_response(session_id, merged, [], intent.type, route=route)

        # 5. Query Rewrite
        queries = await self.query_rewriter.rewrite(query, intent, memory_prompt)
        matched_skills = await self.skill_executor.matching(query, resolved_depts)
        skill_plan = await self.skill_executor.prepare(
            query, queries, matched_skills, session_id=session_id, user_id=user_id
        )

        # 6. Retrieval
        chunks = await self.retrieval_agent.retrieve(skill_plan.queries, resolved_depts, top_k=skill_plan.top_k)
        # 组织记忆只能引导回查官方事实，其 source chunk 与普通检索证据统一去重。
        retrieved_chunks = list(chunks)
        chunks = []
        seen_chunks: set[str] = set()
        for source_chunk in memory_context.evidence_chunks:
            chunk_id = source_chunk.get("_id") or source_chunk.get("id")
            if chunk_id and chunk_id not in seen_chunks:
                chunks.append(source_chunk)
                seen_chunks.add(chunk_id)
        for retrieved in retrieved_chunks:
            chunk_id = retrieved.get("_id") or retrieved.get("id")
            if chunk_id and chunk_id not in seen_chunks:
                chunks.append(retrieved)
                seen_chunks.add(chunk_id)
        chunks = chunks[: skill_plan.top_k]
        await self.working_memory.set_retrieved(session_id, chunks)

        # 7. Answer（注入 Skill workflow 的实际执行结果）
        skill_hints = "；".join(skill_plan.instructions)
        answer = await self.answer_agent.generate(
            query, chunks, rules=rules, intent=intent, extra_instructions=skill_hints,
            memory_context=memory_prompt,
        )
        answer = await self._enrich_citations(answer)

        # 8. Verify（最多打回 2 次）
        for _ in range(MAX_VERIFY_RETRY):
            verdict = await self.verifier_agent.verify(query, answer, chunks)
            answer.verification = verdict.to_dict()
            if verdict.passed:
                break
            logger.info("答案校验未通过，打回重写: %s", verdict.issues)
            answer = await self.answer_agent.generate(
                query, chunks, rules=rules, intent=intent,
                extra_instructions="上次回答存在以下问题，请修正：" + "; ".join(verdict.issues),
                memory_context=memory_prompt,
            )
            answer = await self._enrich_citations(answer)

        # 8.5 更新命中 Skill 的指标（触发次数 + 成功率）
        if matched_skills:
            await self._record_skill_usage(matched_skills, answer.verification.get("passed", True))

        # 9. 收尾：工作记忆 / trace / 自动反馈 / 用户&部门记忆
        trace_id = await self._finalize(session_id, user_id, query, answer, intent.type, chunks, started)
        await self.skill_executor.record_outcome(
            skill_plan.execution_ids, answer.verification.get("passed", True), trace_id
        )
        await self.memory_context_builder.record_usage(memory_context, session_id, trace_id, user_id)
        return self._to_response(session_id, answer, chunks, intent.type, route=route)

    async def answer_stream(self, query: str, session_id: str, user_id: str = "anonymous") -> AsyncIterator[str]:
        """SSE 流式：复用 answer 的完整编排，但答案内容分片输出。"""
        result = await self.answer(query, session_id=session_id, user_id=user_id)
        content = result["answer"]
        # 简易分片（真实流式可在 Answer Agent 层接 LLM stream）
        for i in range(0, len(content), 16):
            yield content[i : i + 16]

    @staticmethod
    def _merge_department_answers(
        results: list[dict[str, Any]], failed: list[str], degraded: Optional[list[str]] = None
    ) -> Answer:
        sections: list[str] = []
        citations: list[Citation] = []
        dept_ids: list[str] = []
        scores: list[float] = []
        for item in results:
            current = list(item.get("dept_ids") or [])
            dept_ids.extend(current)
            label = "、".join(current) or "相关部门"
            sections.append(f"【{label}】\n{item.get('answer', '')}")
            scores.append(float(item.get("confidence", 0.0)))
            for c in item.get("citations") or []:
                citations.append(Citation(
                    doc_id=c.get("doc_id", ""), doc_title=c.get("doc_title", ""),
                    dept_id=c.get("dept_id", ""), chunk_index=c.get("chunk_index", 0),
                    section_path=c.get("section_path", []), snippet=c.get("snippet", ""),
                ))
        if failed:
            sections.append("以下部门暂时不可用，已返回其余部门结果：" + "、".join(failed))
        if degraded:
            sections.append("以下部门服务超时，已使用共享检索降级回答：" + "、".join(degraded))
        return Answer(
            content="\n\n".join(sections), citations=citations,
            dept_ids=sorted(set(dept_ids)), confidence=sum(scores) / len(scores) if scores else 0.0,
            verification={
                "passed": bool(results), "partial": bool(failed),
                "failed_departments": failed, "degraded_departments": degraded or [],
            },
        )

    # ---------- 内部 ----------
    async def _match_active_skills(self, query: str) -> list[dict[str, Any]]:
        """按意图关键词匹配已生效的 Skill。"""
        skills = await self.store.list_skills(status="active")
        return [s for s in skills if any(p in query for p in s.get("trigger", {}).get("intent_patterns", []))]

    @staticmethod
    def _skill_hint_text(matched: list[dict[str, Any]]) -> str:
        if not matched:
            return ""
        return "额外要求：" + "；".join(
            f"命中技能[{s.get('name', '')}]，按 {s.get('action', {}).get('type', 'workflow')} 处理" for s in matched
        )

    async def _record_skill_usage(self, matched_skills: list[dict[str, Any]], passed: bool) -> None:
        """更新命中 Skill 的指标：触发次数 + 成功次数 + 成功率 + 最近触发时间。"""
        now = datetime.now(timezone.utc).isoformat()
        for s in matched_skills:
            metrics.SKILL_TRIGGER.labels(skill=s.get("name", s.get("_id", ""))).inc()
            m = dict(s.get("metrics") or {})
            m["trigger_count"] = int(m.get("trigger_count", 0)) + 1
            m["success_count"] = int(m.get("success_count", 0)) + (1 if passed else 0)
            m["success_rate"] = round(m["success_count"] / m["trigger_count"], 4)
            m["last_triggered"] = now
            s["metrics"] = m
            await self.store.upsert_skill(s)

    @staticmethod
    def _answer_from_pi(pi_result: dict[str, Any]) -> Answer:
        """把 pi 服务返回结果转换为 Answer。"""
        citations = [
            Citation(
                doc_id=c.get("doc_id", ""),
                doc_title=c.get("doc_title", ""),
                dept_id=c.get("dept_id", ""),
                chunk_index=c.get("chunk_index", 0),
                section_path=c.get("section_path", []),
                snippet=c.get("snippet", ""),
            )
            for c in pi_result.get("citations", [])
        ]
        return Answer(
            content=pi_result.get("answer", ""),
            citations=citations,
            dept_ids=pi_result.get("deptIds", []),
            confidence=float(pi_result.get("confidence", 0.8)),
            verification=pi_result.get("verification", {}),
        )

    async def _enrich_citations(self, answer: Answer) -> Answer:
        """为引用补全文档标题与部门信息。"""
        for c in answer.citations:
            if c.doc_id and not c.doc_title:
                doc = await self.store.get_document(c.doc_id)
                if doc:
                    c.doc_title = doc.get("title", c.doc_title)
                    c.dept_id = doc.get("dept_id", c.dept_id)
        return answer

    async def _finalize(
        self,
        session_id: str,
        user_id: str,
        query: str,
        answer: Answer,
        intent_type: str,
        chunks: list[dict[str, Any]],
        started: float,
    ) -> str:
        latency_ms = int((time.perf_counter() - started) * 1000)
        await self.working_memory.append_message(session_id, "assistant", answer.content)

        # 自动反馈（Verifier 结果）
        signal = "verifier_pass" if answer.verification.get("passed", True) else "verifier_fail"
        await self.feedback_agent.collect(
            session_id=session_id,
            user_id=user_id,
            query=query,
            answer=answer,
            signal=signal,
            kind="auto",
            intent_type=intent_type,
        )

        # Trace（供 Loop 回放）
        trace = {
                "session_id": session_id,
                "user_id": user_id,
                "query": query,
                "intent": {"type": intent_type, "depts": answer.dept_ids},
                "retrieved_chunks": chunks[:10],
                "answer": answer.content,
                "citations": [c.to_dict() for c in answer.citations],
                "verification": answer.verification,
                "latency_ms": latency_ms,
                "cost": 0.0,
                "success": answer.verification.get("passed", True),
            }
        await self.loop_engine.record_trace(trace)
        await self.episodic_memory.append_event(
            session_id, user_id, "assistant_message", answer.content, dept_ids=answer.dept_ids,
            trace_id=trace["_id"], metadata={"citations": [c.to_dict() for c in answer.citations]},
        )
        # 确定性滚动摘要，避免额外 LLM 调用；后续可由异步摘要器提升质量。
        context = await self.working_memory.get_context(session_id)
        recent = context.get("messages", [])[-4:]
        summary = " | ".join(f"{m.get('role')}:{m.get('content', '')[:240]}" for m in recent)
        await self.working_memory.set_summary(session_id, summary)
        await self.episodic_memory.update_summary(
            session_id, user_id, summary, entities=context.get("entities") or {},
            citation_ids=[c.to_dict().get("doc_id", "") + ":" + str(c.to_dict().get("chunk_index", 0)) for c in answer.citations],
        )

        # 用户记忆 / 部门热点
        await self.user_memory.record_query(user_id, query, intent_type)
        for dept_id in answer.dept_ids:
            await self.dept_memory.bump_hot_query(dept_id, query)

        metrics.QUERY_TOTAL.labels(dept=",".join(answer.dept_ids) or "all", intent=intent_type).inc()
        metrics.QUERY_LATENCY.labels(dept=",".join(answer.dept_ids) or "all").observe(latency_ms / 1000.0)
        return trace["_id"]

    @staticmethod
    def _to_response(
        session_id: str,
        answer: Answer,
        chunks: list[dict[str, Any]],
        intent_type: str,
        route: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "answer": answer.content,
            "citations": [c.to_dict() for c in answer.citations],
            "dept_ids": answer.dept_ids,
            "confidence": answer.confidence,
            "intent_type": intent_type,
            "verification": answer.verification,
            "retrieved_count": len(chunks),
            "route": route,
        }
