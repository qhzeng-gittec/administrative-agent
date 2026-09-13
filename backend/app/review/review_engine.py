"""审核引擎（Human-in-the-loop Review Engine）。

把 Loop 的"人逐步退出"落地为可观测流程：

1. 新文档入库后，LLM 依据文档内容自动生成测试题（题库积累）。
2. 系统用自身检索+生成链路作答（自测）。
3. 生成"审核单"发给对应部门管理员，逐题判定正确/错误（反馈积累）。
4. 累计正确率超过阈值（默认 0.8）且样本足够后，该部门进入 human_out_of_loop，
   取消人工审核，后续新文档自动通过——实现真正的 Loop 渐进退出。

部门 loop_phase 三态：
- human_in_loop    ：100% 人工审核（起步）
- human_on_loop    ：正确率已达标但样本不足，仍在积累证据
- human_out_of_loop：达到阈值+样本，取消人工审核，系统自动通过
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import Settings
from app.llm.client import ChatMessage, LLMClient
from app.storage.store import DataStore
from app.utils.logging import get_logger

logger = get_logger(__name__)

QUESTION_PROMPT = """你是学校制度文档测试题生成助手。请基于给定文档内容，生成 {n} 道能考察该文档核心条款的问答题。

仅输出 JSON 数组（不要输出任何其它文字）：
[
  {{"question": "具体、可验证的问题", "expected": "正确答案要点"}}
]

要求：
- 问题围绕制度条款、办理条件、时间节点、流程步骤等核心信息。
- 问题必须能从文档内容中直接找到答案，具体可验证。
- 不同问题覆盖不同主题，避免重复。

文档标题：{title}
文档内容：
{content}
"""

SELF_ANSWER_SYSTEM = "你是学校制度咨询助手，回答严谨、有据可依。"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewEngine:
    def __init__(
        self,
        settings: Settings,
        store: DataStore,
        llm: LLMClient,
        retrieval_agent: Any,
        answer_agent: Any,
        rule_engine: Any,
        feedback_collector: Any = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.llm = llm
        self.retrieval_agent = retrieval_agent
        self.answer_agent = answer_agent
        self.rule_engine = rule_engine
        self.feedback_collector = feedback_collector

    # ---------- 出题 ----------
    async def generate_questions(self, doc: dict[str, Any], n: Optional[int] = None) -> list[dict[str, str]]:
        n = n or self.settings.review_question_count
        chunks = await self.store.list_chunks_by_doc(doc["_id"])
        content = self._doc_content(chunks)
        if not content:
            return []
        try:
            msg = ChatMessage.user(QUESTION_PROMPT.format(n=n, title=doc.get("title", ""), content=content))
            data = await self.llm.complete_json(
                [ChatMessage.system("你是测试题生成助手，只输出 JSON。"), msg], temperature=0.4, max_tokens=1500
            )
            if isinstance(data, list):
                return [q for q in data if isinstance(q, dict) and q.get("question")][:n]
        except Exception as exc:  # noqa: BLE001
            logger.warning("测试题生成 LLM 失败(%s)，使用启发式回退", exc)
        return self._heuristic_questions(doc, chunks, n)

    # ---------- 审核单生成 ----------
    async def create_review_order(self, doc: dict[str, Any]) -> Optional[dict[str, Any]]:
        """为新入库文档生成审核单（自动出题 → 系统作答 → 发审核）。"""
        dept_id = doc.get("dept_id", "")
        questions = await self.generate_questions(doc)
        if not questions:
            logger.info("文档 %s 未生成测试题，跳过审核单", doc.get("title", ""))
            return None

        qa_pairs: list[dict[str, Any]] = []
        for q in questions:
            answer = await self._self_answer(q["question"], [dept_id] if dept_id else None)
            qa_pairs.append(
                {
                    "question": q["question"],
                    "expected": q.get("expected", ""),
                    "answer": answer["content"],
                    "citations": answer["citations"],
                    "confidence": answer["confidence"],
                    "trace_id": answer.get("trace_id", ""),
                    "verdict": None,   # None | "auto" | "approved" | "rejected"
                    "correct": None,   # None | True | False
                    "correction": "",
                }
            )

        phase = await self.get_dept_phase(dept_id)
        sample_bucket = int(hashlib.sha256(doc.get("_id", "").encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        sampled_review = phase == "human_out_of_loop" and sample_bucket < self.settings.review_sample_rate
        auto = phase == "human_out_of_loop" and not sampled_review
        order: dict[str, Any] = {
            "_id": "review_" + uuid.uuid4().hex,
            "dept_id": dept_id,
            "doc_id": doc.get("_id", ""),
            "doc_title": doc.get("title", ""),
            "status": "auto_approved" if auto else "pending",
            "qa_pairs": qa_pairs,
            "total": len(qa_pairs),
            "correct": len(qa_pairs) if auto else 0,
            "accuracy": 1.0 if auto else None,
            "loop_phase_at_create": phase,
            "sampled_review": sampled_review,
            "created_at": _now(),
            "reviewed_at": _now() if auto else None,
            "reviewed_by": "system(auto)" if auto else None,
        }
        if auto:
            for pair in qa_pairs:
                pair["verdict"] = "auto"
                pair["correct"] = True
            # 自动通过后也写入题库（反馈积累）
            for pair in qa_pairs:
                await self._add_test_question(dept_id, doc, pair, verdict="auto", correct=True)

        await self.store.insert_review_order(order)
        logger.info("已生成审核单 %s (dept=%s, phase=%s, %d 题)", order["_id"], dept_id, phase, len(qa_pairs))
        return order

    # ---------- 提交审核 ----------
    async def submit_review(
        self, order_id: str, verdicts: list[dict[str, Any]], reviewer: str
    ) -> dict[str, Any]:
        order = await self.store.get_review_order(order_id)
        if order is None:
            raise KeyError(f"审核单不存在: {order_id}")
        if order.get("status") not in ("pending",):
            return order

        pairs: list[dict[str, Any]] = order["qa_pairs"]
        by_index = {v.get("index"): v for v in verdicts if isinstance(v.get("index"), int)}
        expected_indexes = set(range(len(pairs)))
        if set(by_index) != expected_indexes:
            missing = sorted(expected_indexes - set(by_index))
            extra = sorted(set(by_index) - expected_indexes)
            raise ValueError(f"必须逐题审核全部题目；缺失={missing}，越界={extra}")
        correct = 0
        for i, pair in enumerate(pairs):
            v = by_index.get(i)
            assert v is not None
            is_correct = bool(v.get("correct", False))
            pair["verdict"] = "approved" if is_correct else "rejected"
            pair["correct"] = is_correct
            pair["correction"] = v.get("correction", "") if not is_correct else ""
            if is_correct:
                correct += 1
            # 写入题库 + 反馈
            await self._add_test_question(
                order.get("dept_id", ""), {"_id": order.get("doc_id", ""), "title": order.get("doc_title", "")},
                pair, verdict=pair["verdict"], correct=is_correct,
            )
            if not is_correct and self.feedback_collector is not None:
                await self.feedback_collector.collect_explicit(
                    session_id=f"review:{order_id}", user_id=reviewer, query=pair.get("question", ""),
                    answer=pair.get("answer", ""), signal="correction",
                    detail={
                        "source": "review", "review_order_id": order_id,
                        "trace_id": pair.get("trace_id", ""), "dept_id": order.get("dept_id", ""),
                        "correction": pair.get("correction", ""),
                    },
                )

        total = len(pairs)
        accuracy = correct / max(total, 1)
        order["correct"] = correct
        order["accuracy"] = round(accuracy, 4)
        order["status"] = "reviewed"
        order["reviewed_at"] = _now()
        order["reviewed_by"] = reviewer
        await self.store.insert_review_order(order)

        # 更新部门审核统计 + 渐进退出判定
        dept_id = order.get("dept_id", "")
        await self._update_dept_review_stats(dept_id, total, correct)
        new_phase = await self.maybe_fade_out(dept_id)
        # 环外抽检发现错误时立即回退到人在环上，阻止继续自动放行。
        if order.get("sampled_review") and correct < total:
            dept = await self.store.get_department(dept_id)
            if dept:
                dept["loop_phase"] = "human_on_loop"
                dept["rollback_reason"] = f"抽检审核单 {order_id} 发现 {total - correct} 个错误"
                dept["updated_at"] = _now()
                await self.store.upsert_department(dept)
                new_phase = "human_on_loop"
        logger.info("审核单 %s 完成：%d/%d (%.2f), 部门 %s → %s", order_id, correct, total, accuracy, dept_id, new_phase)
        return order

    # ---------- 部门审核统计与渐进退出 ----------
    async def get_dept_phase(self, dept_id: str) -> str:
        dept = await self.store.get_department(dept_id) if dept_id else None
        if dept is None:
            return "human_in_loop"
        return dept.get("loop_phase", "human_in_loop")

    async def dept_review_stats(self, dept_id: str) -> dict[str, Any]:
        dept = await self.store.get_department(dept_id) if dept_id else None
        stats = (dept or {}).get("review_stats") or {"total": 0, "correct": 0, "accuracy": 0.0}
        stats.setdefault("accuracy", stats.get("correct", 0) / max(stats.get("total", 0), 1))
        return stats

    async def _update_dept_review_stats(self, dept_id: str, total: int, correct: int) -> None:
        if not dept_id:
            return
        dept = await self.store.get_department(dept_id)
        if dept is None:
            return
        stats = dept.get("review_stats") or {"total": 0, "correct": 0}
        stats["total"] = stats.get("total", 0) + total
        stats["correct"] = stats.get("correct", 0) + correct
        stats["accuracy"] = round(stats["correct"] / max(stats["total"], 1), 4)
        dept["review_stats"] = stats
        await self.store.upsert_department(dept)

    async def maybe_fade_out(self, dept_id: str) -> str:
        """依据累计正确率与样本量推进部门 loop_phase（人逐步退出）。"""
        if not dept_id:
            return "human_in_loop"
        dept = await self.store.get_department(dept_id)
        if dept is None:
            return "human_in_loop"
        stats = dept.get("review_stats") or {"total": 0, "correct": 0, "accuracy": 0.0}
        accuracy = float(stats.get("accuracy", 0.0))
        samples = int(stats.get("total", 0))
        threshold = self.settings.review_accuracy_threshold
        min_samples = self.settings.review_min_samples

        old_phase = dept.get("loop_phase", "human_in_loop")
        if accuracy >= threshold and samples >= min_samples:
            new_phase = "human_out_of_loop"
        elif accuracy >= threshold:
            new_phase = "human_on_loop"
        else:
            new_phase = "human_in_loop"

        if new_phase != old_phase:
            dept["loop_phase"] = new_phase
            dept["updated_at"] = _now()
            dept["fade_out"] = {
                "achieved_at": _now(),
                "accuracy": accuracy,
                "samples": samples,
                "reason": f"正确率 {accuracy:.0%} ≥ {threshold:.0%} 且样本 {samples} ≥ {min_samples}",
            } if new_phase == "human_out_of_loop" else dept.get("fade_out")
            await self.store.upsert_department(dept)
            logger.info("部门 %s Loop 阶段推进: %s → %s (accuracy=%.2f, samples=%d)", dept_id, old_phase, new_phase, accuracy, samples)
        return new_phase

    # ---------- 内部 ----------
    async def _self_answer(self, query: str, dept_ids: Optional[list[str]]) -> dict[str, Any]:
        try:
            chunks = await self.retrieval_agent.retrieve([query], dept_ids, top_k=5)
            rules = await self.rule_engine.active_rules(dept_ids)
            answer = await self.answer_agent.generate(query, chunks, rules=rules)
            trace_id = "trace_review_" + uuid.uuid4().hex
            await self.store.insert_trace({
                "_id": trace_id, "session_id": "review", "user_id": "review_engine",
                "query": query, "intent": {"type": "review", "depts": dept_ids or []},
                "retrieved_chunks": chunks[:10], "answer": answer.content,
                "citations": [c.to_dict() for c in answer.citations], "verification": {},
                "latency_ms": 0, "cost": 0.0, "success": True, "created_at": _now(),
            })
            return {
                "content": answer.content,
                "citations": [c.to_dict() for c in answer.citations],
                "confidence": answer.confidence,
                "trace_id": trace_id,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("系统自测作答失败(%s): %s", query, exc)
            return {"content": "（系统自测作答失败，请人工判定）", "citations": [], "confidence": 0.0}

    async def _add_test_question(
        self, dept_id: str, doc: dict[str, Any], pair: dict[str, Any], verdict: str, correct: bool
    ) -> None:
        await self.store.insert_test_question(
            {
                "_id": "tq_" + uuid.uuid4().hex,
                "dept_id": dept_id,
                "doc_id": doc.get("_id", ""),
                "doc_title": doc.get("title", ""),
                "question": pair.get("question", ""),
                "expected": pair.get("expected", ""),
                "answer": pair.get("answer", ""),
                "verdict": verdict,
                "correct": correct,
                "correction": pair.get("correction", ""),
                "created_at": _now(),
            }
        )

    @staticmethod
    def _doc_content(chunks: list[dict[str, Any]], limit: int = 6000) -> str:
        parts: list[str] = []
        total = 0
        for c in chunks:
            text = c.get("content", "")
            if total + len(text) > limit:
                parts.append(text[: max(0, limit - total)])
                break
            parts.append(text)
            total += len(text)
        return "\n\n".join(parts)

    @staticmethod
    def _heuristic_questions(doc: dict[str, Any], chunks: list[dict[str, Any]], n: int) -> list[dict[str, str]]:
        questions: list[dict[str, str]] = []
        for c in chunks[:n]:
            title = c.get("section_title") or (c.get("section_path") or ["相关条款"])[-1]
            if not c.get("content", "").strip():
                continue
            questions.append(
                {
                    "question": f"《{doc.get('title', '本文档')}》中关于「{title}」是如何规定的？",
                    "expected": c.get("content", "")[:120],
                }
            )
        return questions[:n]
