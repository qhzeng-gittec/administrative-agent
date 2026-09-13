"""Loop Engine：Execute → Observe → Reflect → Adapt → Deploy 五阶段循环。"""
from __future__ import annotations

import uuid
import copy
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from app.config import Settings
from app.llm.client import ChatMessage, LLMClient
from app.llm.embeddings import EmbeddingClient
from app.loop.feedback_collector import FeedbackCollector
from app.loop.hook_engine import HookEngine
from app.loop.rule_engine import RuleEngine
from app.loop.skill_miner import SkillMiner
from app.loop.strategy_evaluator import StrategyEvaluator
from app.storage.store import DataStore
from app.utils.logging import get_logger
from app.integrations.pi_runtime import PiAgentRuntimeClient

logger = get_logger(__name__)

BAD_SIGNALS = {"down", "correction", "verifier_fail", "follow_up"}

REFLECT_PROMPT = """你是 Loop 反思助手。分析以下 bad case，判断根因并给出可执行的改进建议。

bad case：
{cases}

仅输出 JSON：
{{
  "root_causes": {{"retrieval": 0, "intent": 0, "generation": 0, "knowledge_gap": 0}},
  "suggestions": [
    {{"type": "rule|hook|skill", "title": "...", "detail": "..."}}
  ]
}}

根因分类：
- retrieval：chunk 没切好 / embedding 不准 / 关键词没扩展
- intent：部门路由错 / 问题类型判断错
- generation：幻觉 / 没引用 / 答非所问
- knowledge_gap：文档里确实没有这个内容
"""


class LoopEngine:
    def __init__(
        self,
        settings: Settings,
        store: DataStore,
        llm: LLMClient,
        embeddings: EmbeddingClient,
        skill_miner: SkillMiner,
        hook_engine: HookEngine,
        rule_engine: RuleEngine,
        feedback_collector: FeedbackCollector,
        strategy_evaluator: StrategyEvaluator,
        pi_runtime: PiAgentRuntimeClient | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.llm = llm
        self.embeddings = embeddings
        self.skill_miner = skill_miner
        self.hook_engine = hook_engine
        self.rule_engine = rule_engine
        self.feedback_collector = feedback_collector
        self.strategy_evaluator = strategy_evaluator
        self.pi_runtime = pi_runtime

    # ---------- Execute ----------
    async def record_trace(self, trace: dict[str, Any]) -> None:
        trace.setdefault("_id", "trace_" + uuid.uuid4().hex)
        trace.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        await self.store.insert_trace(trace)

    # ---------- 完整循环 ----------
    async def run_cycle(
        self, progress_callback: Callable[[str, str], Awaitable[None]] | None = None
    ) -> dict[str, Any]:
        if not self.settings.loop_enabled:
            return {"skipped": True, "reason": "loop disabled"}

        started_at = datetime.now(timezone.utc)
        before = await self._artifact_counts()

        async def progress(stage: str, detail: str) -> None:
            if progress_callback is not None:
                await progress_callback(stage, detail)

        # Observe
        await progress("observe", "正在读取显式反馈、隐式行为和 Verifier 信号")
        pending = await self.feedback_collector.pending()
        bad_cases = [p for p in pending if p.get("signal") in BAD_SIGNALS]
        signal_counts = dict(Counter(str(p.get("signal", "unknown")) for p in pending))
        report: dict[str, Any] = {
            "cycle_id": "loop_cycle_" + uuid.uuid4().hex[:12],
            "started_at": started_at.isoformat(),
            "observed": len(pending), "bad_cases": len(bad_cases), "signals": signal_counts,
        }

        # Reflect
        await progress("reflect", f"正在归因 {len(bad_cases)} 个 bad cases")
        reflect = await self._reflect(bad_cases)
        report["reflect"] = reflect

        # Adapt
        await progress("adapt", "正在回放历史 Trace 并生成 Skill / Hook / Rule 候选")
        adaptations = await self._adapt(reflect, bad_cases)
        report["adaptations"] = adaptations

        # Deploy
        await progress("deploy", "正在依据人在环阶段审核、灰度发布或保留候选")
        report["deployed"] = await self._deploy()

        # 消费已处理反馈
        await self.feedback_collector.consume([p["_id"] for p in pending])
        after = await self._artifact_counts()
        completed_at = datetime.now(timezone.utc)
        report.update({
            "completed_at": completed_at.isoformat(),
            "duration_ms": int((completed_at - started_at).total_seconds() * 1000),
            "before": before, "after": after,
            "changes": {key: after[key] - before[key] for key in before},
            "summary": self._cycle_summary(len(pending), len(bad_cases), adaptations, report["deployed"]),
            "next_action": self._next_action(len(pending), adaptations),
        })
        await progress("complete", report["summary"])
        logger.info("Loop 循环完成: %s", report)
        return report

    async def _artifact_counts(self) -> dict[str, int]:
        skills = await self.store.list_skills()
        hooks = await self.store.list_hooks()
        rules = await self.store.list_rules()
        return {
            "skills": len(skills), "active_skills": sum(s.get("status") == "active" for s in skills),
            "pending_skills": sum(s.get("status") == "pending" for s in skills),
            "hooks": len(hooks), "rules": len(rules),
            "experiments": await self.store.count("experiments"),
            "strategy_versions": await self.store.count("strategy_versions"),
        }

    @staticmethod
    def _cycle_summary(observed: int, bad_cases: int, adaptations: list[dict[str, Any]], deployed: dict[str, Any]) -> str:
        deployed_total = sum(int(deployed.get(key, 0)) for key in ("skills", "hooks", "rules"))
        if observed == 0:
            return "本轮没有待处理反馈；已完成策略健康检查和生命周期维护。"
        return f"已分析 {observed} 条反馈（{bad_cases} 个 bad case），生成 {len(adaptations)} 个候选，发布 {deployed_total} 个策略产物。"

    @staticmethod
    def _next_action(observed: int, adaptations: list[dict[str, Any]]) -> str:
        if observed == 0:
            return "先在学生端完成几次问答并提交有帮助/需改进反馈，再触发 Loop 查看行为变化。"
        if adaptations:
            return "检查下方新生成的 Skill/Rule/Hook；人在环中阶段需要管理员审核后才会生效。"
        return "当前反馈尚未形成稳定策略候选；继续积累相似问题和纠错样本。"

    async def _reflect(self, bad_cases: list[dict[str, Any]]) -> dict[str, Any]:
        if not bad_cases:
            return {"root_causes": {}, "suggestions": []}
        # 确定性建议：从 badcase 携带的违规规则(detail.rule)抽取，保证 Adapt 一定有产出
        suggestions = self._deterministic_suggestions(bad_cases)
        root_causes: dict[str, Any] = {}
        # 尝试 pi Reflect Agent；不可用时回退 Python LLM，再失败则启发式。
        try:
            case_text = "\n".join(f"- Q: {c.get('query', '')}\n  A: {c.get('answer', '')[:150]}" for c in bad_cases[:10])
            prompt = REFLECT_PROMPT.format(cases=case_text)
            data = None
            if self.pi_runtime is not None:
                data = await self.pi_runtime.run_json(
                    "reflect", "你是 Loop 反思助手，只提出候选建议，不得直接发布策略。", prompt,
                    timeout_seconds=self.settings.pi_runtime_timeout_reflect,
                )
            if not isinstance(data, dict):
                msg = ChatMessage.user(prompt)
                data = await self.llm.complete_json(
                    [ChatMessage.system("你是 Loop 反思助手。"), msg], temperature=0.0
                )
            if isinstance(data, dict):
                root_causes = data.get("root_causes") or {}
                existing = {s.get("detail", "") for s in suggestions}
                for extra in (data.get("suggestions") or []):
                    if extra.get("detail") and extra.get("detail") not in existing:
                        existing.add(extra["detail"])
                        suggestions.append(extra)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Reflect LLM 失败(%s)，使用启发式归因", exc)
        if not root_causes:
            root_causes = self._heuristic_root_causes(bad_cases)
        return {"root_causes": root_causes, "suggestions": suggestions}

    @staticmethod
    def _heuristic_root_causes(bad_cases: list[dict[str, Any]]) -> dict[str, Any]:
        causes = Counter()
        for c in bad_cases:
            if c.get("signal") == "verifier_fail":
                causes["generation"] += 1
            elif c.get("signal") == "correction":
                causes["retrieval"] += 1
            else:
                causes["knowledge_gap"] += 1
        return dict(causes)

    @staticmethod
    def _deterministic_suggestions(bad_cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """从 badcase 的 detail.rule 抽取确定性 rubric 规则建议（带部门）。"""
        suggestions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for c in bad_cases:
            detail = c.get("detail") or {}
            rule = (detail.get("rule") or "").strip()
            if rule and rule not in seen:
                seen.add(rule)
                suggestions.append(
                    {"type": "rule", "title": rule[:20], "detail": rule, "dept_id": detail.get("dept_id", "")}
                )
        return suggestions

    async def _adapt(self, reflect: dict[str, Any], bad_cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
        adaptations: list[dict[str, Any]] = []
        phase = self.settings.loop_phase
        auto = phase == "human_out_of_loop"
        on_loop = phase == "human_on_loop"

        # 1) Skill 挖掘（从近期 trace 聚类）
        try:
            traces = await self.store.list_recent_traces(limit=500)
            queries = [t.get("query", "") for t in traces]
            vecs = await self.embeddings.embed(queries)
            emb_map = {q: v for q, v in zip(queries, vecs)}
            drafts = await self.skill_miner.mine(traces, emb_map)
            for d in drafts:
                same_name = [s for s in await self.store.list_skills() if s.get("name") == d.get("name")]
                if same_name:
                    latest = max(same_name, key=lambda s: int(s.get("version", 1)))
                    d["version"] = int(latest.get("version", 1)) + 1
                    d["replaces"] = latest.get("_id")
                replay = await self.strategy_evaluator.replay_skill(d, traces)
                success = replay["candidate_score"]
                d["metrics"]["success_rate"] = success
                d["replay"] = replay
                # 人在环中：一律待人工审核；人在环上：回测成功率达标才自动生效；人在环外：全自动
                activate = (auto or (on_loop and success >= self.settings.skill_sandbox_min_success)) and replay["passed"]
                # 灰度：稳定哈希分桶，真实记录 treatment/control。
                d["gray_percent"] = self.settings.loop_gray_percent
                d["experiment_id"] = "exp_" + uuid.uuid4().hex
                await self.skill_miner.register(d, auto_activate=activate)
                await self._snapshot_strategy(d, "candidate_created")
                await self.store.upsert("experiments", {
                    "_id": d["experiment_id"], "artifact_id": d.get("_id"), "artifact_type": "skill",
                    "version": d.get("version", 1), "gray_percent": d["gray_percent"],
                    "status": "running" if activate else "pending_review", "replay": replay,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                adaptations.append({"type": "skill", "id": d.get("_id"), "name": d.get("name"), "auto_activated": activate})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skill 挖掘失败: %s", exc)

        # 2) Rule / Hook 建议（来自 Reflect）
        for s in (reflect.get("suggestions") or [])[:50]:
            s_type = s.get("type", "rule")
            dept_id = s.get("dept_id", "")
            if s_type == "rule":
                rule = {
                    "_id": "rule_" + uuid.uuid4().hex,
                    "name": "auto_rule",
                    "scope": "department" if dept_id else "global",
                    "dept_id": dept_id,
                    "content": s.get("detail", ""),
                    "priority": 50,
                    "status": "pending",
                    "auto_generated": True,
                    "confidence": 0.6,
                    "created_by": "loop_engine",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                await self.rule_engine.register(rule, auto_activate=auto)
                # 把反思出的 rubric 规则沉淀到对应部门 Skill 的 rubric_rules
                if dept_id:
                    await self._append_skill_rubric(dept_id, s.get("detail", ""))
                adaptations.append({"type": "rule", "id": rule["_id"], "dept_id": dept_id, "auto_activated": auto})
            elif s_type == "hook":
                hook = {
                    "_id": "hook_" + uuid.uuid4().hex,
                    "name": "auto_hook",
                    "scope": "global",
                    "trigger": {"intent_patterns": [s.get("title", "")]},
                    "action": {"type": "cross_dept_retrieval"},
                    "status": "pending",
                    "auto_generated": True,
                    "confidence": 0.6,
                    "created_by": "loop_engine",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                await self.hook_engine.register(hook, auto_activate=auto)
                adaptations.append({"type": "hook", "id": hook["_id"], "auto_activated": auto})
        await self._maintain_skill_lifecycle()
        return adaptations

    async def _append_skill_rubric(self, dept_id: str, rule: str) -> None:
        """把新反思出的 rubric 规则追加到对应部门初始 Skill 的 rubric_rules（去重）。"""
        if not dept_id or not rule:
            return
        skill = await self.store.get_skill(f"skill_{dept_id}_seed")
        if skill is None:
            return
        rubrics = list(skill.get("rubric_rules") or [])
        if rule not in rubrics:
            rubrics.append(rule)
            skill["rubric_rules"] = rubrics
            skill["updated_at"] = datetime.now(timezone.utc).isoformat()
            await self.store.upsert_skill(skill)
            logger.info("已把 rubric 规则写入部门 Skill《%s》: %s", skill.get("name", ""), rule[:40])

    async def _deploy(self) -> dict[str, Any]:
        """Deploy：高置信度自动生效（人在环上/环外），否则留待审核。"""
        threshold = self.settings.hook_high_confidence
        phase = self.settings.loop_phase
        auto = phase == "human_out_of_loop"
        deployed: dict[str, Any] = {"skills": 0, "hooks": 0, "rules": 0}

        if phase == "human_in_loop":
            return deployed  # 全部人工审核

        for skill in await self.store.list_skills(status="pending"):
            if auto or (skill.get("confidence", 0) >= threshold):
                skill["status"] = "active"
                await self.store.upsert_skill(skill)
                await self._snapshot_strategy(skill, "activated")
                replaced = skill.get("replaces")
                if replaced:
                    old = await self.store.get_skill(replaced)
                    if old and old.get("status") == "active":
                        old["status"] = "deprecated"
                        old["deprecated_by"] = skill.get("_id")
                        await self.store.upsert_skill(old)
                deployed["skills"] += 1
        for hook in await self.store.list_hooks(status="pending"):
            if auto or (hook.get("confidence", 0) >= threshold):
                hook["status"] = "active"
                await self.store.upsert_hook(hook)
                deployed["hooks"] += 1
        for rule in await self.store.list_rules(status="pending"):
            if auto or (rule.get("confidence", 0) >= threshold):
                rule["status"] = "active"
                await self.store.upsert_rule(rule)
                deployed["rules"] += 1
        rolled_back = await self._rollback_failed_experiments()
        deployed["rolled_back"] = rolled_back
        return deployed

    async def _snapshot_strategy(self, artifact: dict[str, Any], reason: str) -> None:
        snapshot = copy.deepcopy(artifact)
        await self.store.upsert("strategy_versions", {
            "_id": f"strategy_version_{artifact.get('_id')}_{artifact.get('version', 1)}_{uuid.uuid4().hex[:8]}",
            "artifact_id": artifact.get("_id"), "artifact_type": "skill",
            "version": artifact.get("version", 1), "reason": reason, "snapshot": snapshot,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    async def _rollback_failed_experiments(self) -> int:
        rolled_back = 0
        for experiment in await self.store.find("experiments", {"status": "running"}):
            rows = await self.store.find("strategy_executions", {"artifact_id": experiment.get("artifact_id")})
            complete = [r for r in rows if r.get("success") is not None]
            treatment = [r for r in complete if r.get("group") == "treatment"]
            control = [r for r in complete if r.get("group") == "control"]
            minimum = self.settings.loop_rollback_min_samples
            if len(treatment) < minimum or len(control) < minimum:
                continue
            treatment_rate = sum(bool(r["success"]) for r in treatment) / len(treatment)
            control_rate = sum(bool(r["success"]) for r in control) / len(control)
            experiment.update({
                "treatment_success": treatment_rate, "control_success": control_rate,
                "sample_count": len(complete), "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            if treatment_rate + self.settings.loop_rollback_margin < control_rate:
                skill = await self.store.get_skill(experiment["artifact_id"])
                if skill:
                    skill["status"] = "deprecated"
                    skill["rollback_reason"] = "treatment_underperformed_control"
                    await self.store.upsert_skill(skill)
                experiment["status"] = "rolled_back"
                rolled_back += 1
            await self.store.upsert("experiments", experiment)
        return rolled_back

    async def _maintain_skill_lifecycle(self) -> None:
        skills = await self.store.list_skills(status="active")
        # 合并建议：触发模式高度重叠且 action 相同。
        existing = {
            (p.get("kind"), tuple(sorted(p.get("skill_ids", []))))
            for p in await self.store.find("strategy_proposals", {"status": "pending"})
        }
        for i, left in enumerate(skills):
            left_patterns = set(left.get("trigger", {}).get("intent_patterns", []))
            metrics = left.get("metrics") or {}
            created_raw = left.get("created_at") or ""
            try:
                created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            except ValueError:
                created = datetime.now(timezone.utc)
            if metrics.get("trigger_count", 0) < 5 and created < datetime.now(timezone.utc) - timedelta(days=14):
                left["status"] = "stale"
                left["stale_reason"] = "14天内触发量不足5次"
                await self.store.upsert_skill(left)
                continue
            if metrics.get("trigger_count", 0) >= 20 and metrics.get("success_rate", 1.0) < 0.6:
                key = ("split", (left.get("_id"),))
                if key not in existing:
                    await self._proposal("split", [left.get("_id")], "高频但成功率低，建议按查询簇拆分")
                left["status"] = "stale"
                left["stale_reason"] = "连续高频触发但成功率低于60%"
                await self.store.upsert_skill(left)
                continue
            for right in skills[i + 1:]:
                right_patterns = set(right.get("trigger", {}).get("intent_patterns", []))
                union = left_patterns | right_patterns
                overlap = len(left_patterns & right_patterns) / len(union) if union else 0.0
                ids = tuple(sorted([left.get("_id"), right.get("_id")]))
                if overlap > 0.8 and left.get("action") == right.get("action") and ("merge", ids) not in existing:
                    await self._proposal("merge", list(ids), f"触发 Jaccard={overlap:.2f} 且工作流一致")

    async def _proposal(self, kind: str, skill_ids: list[str], reason: str) -> None:
        await self.store.upsert("strategy_proposals", {
            "_id": "proposal_" + uuid.uuid4().hex, "kind": kind, "skill_ids": skill_ids,
            "reason": reason, "status": "pending", "created_at": datetime.now(timezone.utc).isoformat(),
        })

    async def stats(self) -> dict[str, Any]:
        fb = await self.feedback_collector.stats()
        traces = await self.store.list_recent_traces(limit=1000)
        skills = await self.store.list_skills()
        return {
            "feedback": fb,
            "trace_count": len(traces),
            "skill_count": len(skills),
            "active_skills": sum(1 for s in skills if s.get("status") == "active"),
        }
