"""Skill Miner：对 trace 做 embedding 聚类（DBSCAN），LLM 生成 Skill 草稿，沙箱回测。"""
from __future__ import annotations

import math
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from app.llm.client import ChatMessage, LLMClient
from app.retrieval.bm25 import tokenize
from app.storage.store import DataStore
from app.utils.logging import get_logger

logger = get_logger(__name__)

SKILL_DRAFT_PROMPT = """你是 Skill 挖掘助手。根据一类高频问题模式，生成一个可复用的 Skill 草稿。

仅输出 JSON：
{{
  "name": "技能名(中文)",
  "trigger": {{"intent_patterns": ["关键词1", "关键词2"], "entities_required": ["matter"], "confidence_threshold": 0.75}},
  "action": {{
    "type": "workflow",
    "steps": [
      {{"step": 1, "action": "extract_entity", "params": {{"entity": "matter"}}}},
      {{"step": 2, "action": "retrieve", "params": {{"query": "{{matter}} 相关制度", "top_k": 5}}}},
      {{"step": 3, "action": "generate", "params": {{"template": "default"}}}}
    ]
  }}
}}

问题模式样例：
{queries}

请基于样例抽取触发意图模式与执行步骤。
"""


class SkillMiner:
    def __init__(self, store: DataStore, llm: Optional[LLMClient] = None, min_cluster: int = 20) -> None:
        self.store = store
        self.llm = llm
        self.min_cluster = min_cluster

    def cluster(self, queries: list[str], embeddings: list[list[float]]) -> list[list[int]]:
        """DBSCAN 聚类，返回各簇内下标列表。"""
        if len(queries) < 2:
            return []
        try:
            from sklearn.cluster import DBSCAN  # 延迟导入

            dist = self._cosine_distance_matrix(embeddings)
            eps = 0.35  # cosine 距离阈值（cosine>0.65 视为同簇）
            labels = DBSCAN(eps=eps, min_samples=2, metric="precomputed").fit_predict(dist)
            clusters: dict[int, list[int]] = {}
            for i, lab in enumerate(labels):
                if lab != -1:
                    clusters.setdefault(int(lab), []).append(i)
            return list(clusters.values())
        except Exception as exc:  # noqa: BLE001
            logger.warning("DBSCAN 聚类失败(%s)，回退关键词分组", exc)
            return self._keyword_group(queries)

    async def mine(self, traces: list[dict[str, Any]], embeddings: dict[str, list[float]]) -> list[dict[str, Any]]:
        """从 trace 挖掘 Skill 草稿（仅处理满足最小簇规模的模式）。"""
        queries = [t.get("query", "") for t in traces]
        clusters = self.cluster(queries, [embeddings.get(q, self._dummy_vec()) for q in queries])
        drafts: list[dict[str, Any]] = []
        for cluster in clusters:
            if len(cluster) < self.min_cluster:
                continue
            cluster_queries = [queries[i] for i in cluster]
            draft = await self._generate_draft(cluster_queries)
            draft["metrics"] = {"trigger_count": len(cluster), "success_rate": 0.0, "avg_latency_ms": 0, "last_triggered": ""}
            draft["version"] = 1
            draft["status"] = "pending"
            draft["auto_generated"] = True
            draft["confidence"] = min(0.95, 0.5 + 0.02 * len(cluster))
            draft["created_by"] = "loop_engine"
            draft["created_at"] = datetime.now(timezone.utc).isoformat()
            drafts.append(draft)
        return drafts

    async def backtest(self, skill: dict[str, Any], traces: list[dict[str, Any]]) -> float:
        """沙箱回测：用历史 trace 回放 skill 触发条件，估计成功率（成功回答占比）。"""
        if not traces:
            return 0.0
        patterns = skill.get("trigger", {}).get("intent_patterns", [])
        triggered = [t for t in traces if any(p in t.get("query", "") for p in patterns)]
        if not triggered:
            return 0.0
        success = sum(1 for t in triggered if t.get("success", True))
        return success / len(triggered)

    async def register(self, skill: dict[str, Any], auto_activate: bool = False) -> None:
        skill.setdefault("_id", "skill_" + uuid.uuid4().hex)
        skill["status"] = "active" if auto_activate else skill.get("status", "pending")
        await self.store.upsert_skill(skill)

    async def _generate_draft(self, queries: list[str]) -> dict[str, Any]:
        sample = "\n".join(queries[:15])
        if self.llm is not None:
            try:
                msg = ChatMessage.user(SKILL_DRAFT_PROMPT.format(queries=sample))
                data = await self.llm.complete_json([ChatMessage.system("你是 Skill 挖掘助手。"), msg], temperature=0.2)
                if isinstance(data, dict):
                    return data
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skill 草稿生成失败(%s)，使用关键词回退", exc)
        return self._heuristic_draft(queries)

    @staticmethod
    def _heuristic_draft(queries: list[str]) -> dict[str, Any]:
        counter = Counter()
        for q in queries:
            counter.update(tokenize(q))
        keywords = [w for w, _ in counter.most_common(6)]
        return {
            "name": "高频问题_" + (keywords[0] if keywords else "general"),
            "trigger": {"intent_patterns": keywords, "entities_required": ["matter"], "confidence_threshold": 0.75},
            "action": {
                "type": "workflow",
                "steps": [
                    {"step": 1, "action": "retrieve", "params": {"query": "{matter} 相关制度", "top_k": 5}},
                    {"step": 2, "action": "generate", "params": {"template": "default"}},
                ],
            },
        }

    @staticmethod
    def _dummy_vec() -> list[float]:
        return [0.0] * 128

    @staticmethod
    def _keyword_group(queries: list[str]) -> list[list[int]]:
        groups: dict[str, list[int]] = {}
        for i, q in enumerate(queries):
            toks = tokenize(q)
            key = toks[0] if toks else "empty"
            groups.setdefault(key, []).append(i)
        return [v for v in groups.values() if len(v) >= 2]

    @staticmethod
    def _cosine_distance_matrix(vectors: list[list[float]]) -> list[list[float]]:
        n = len(vectors)
        mat = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                mat[i][j] = 1.0 - SkillMiner._cosine(vectors[i], vectors[j])
        return mat

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(x * x for x in b)) or 1.0
        return dot / (na * nb)
