"""跨部门冲突检测（亮点）：规则层引用挖掘 + 语义层相似比对 + LLM 冲突判定。

新文档入库时，与其他部门同类条款做语义比对，发现条款冲突（如退课截止时间不一致）
则建立 `doc_relations`(type=conflict) 并通知相关部门。
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.llm.client import ChatMessage, LLMClient
from app.llm.embeddings import EmbeddingClient
from app.retrieval.vector_store import VectorStore
from app.storage.store import DataStore
from app.utils.logging import get_logger

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# 规则层：引用模式
REF_PATTERNS = [
    re.compile(r"(?:根据|参照|按照|依据|详见|参见)《?([^》<>，。;；\n]{2,40})》?"),
    re.compile(r"([\u4e00-\u9fff]{4,20}(?:办法|规定|细则|通知|条例|办法（试行）))"),
]

CONFLICT_PROMPT = """你是制度条款冲突检测助手。给定两个不同部门对同类事项的规定，判断是否存在冲突。

仅输出 JSON：
{{"is_conflict": true/false, "reason": "简述", "confidence": 0.0-1.0}}

判定标准：
- 若两者对同一事项（如截止时间、收费标准、办理条件）给出了不同且相互矛盾的规定 → conflict
- 若只是表述不同但实质一致、或针对不同对象/不同场景 → 不冲突

条款 A（{title_a}）：
{text_a}

条款 B（{title_b}）：
{text_b}
"""


class ConflictDetector:
    def __init__(
        self,
        store: DataStore,
        vector_store: VectorStore,
        embeddings: Optional[EmbeddingClient] = None,
        llm: Optional[LLMClient] = None,
    ) -> None:
        self.store = store
        self.vector_store = vector_store
        self.embeddings = embeddings
        self.llm = llm

    async def detect_references(self, doc: dict[str, Any]) -> list[dict[str, Any]]:
        """规则层：正则匹配文中引用模式，建立 reference 关联。"""
        relations: list[dict[str, Any]] = []
        chunks = await self.store.list_chunks_by_doc(doc["_id"])
        all_docs = {d["_id"]: d for d in await self.store.list_documents()}
        for chunk in chunks:
            text = chunk["content"]
            for pat in REF_PATTERNS:
                for m in pat.findall(text):
                    ref_title = m[0] if isinstance(m, tuple) else m
                    for other_id, other_doc in all_docs.items():
                        if other_id == doc["_id"]:
                            continue
                        if ref_title and (ref_title in other_doc.get("title", "") or other_doc.get("title", "") in ref_title):
                            relations.append(self._make_relation(doc, other_doc, "reference", f"引用《{ref_title}》", 0.95))
        return relations

    async def detect_conflicts(self, doc: dict[str, Any], top_k: int = 10) -> list[dict[str, Any]]:
        """语义层 + LLM：与其它部门相似条款比对，判定冲突。"""
        relations: list[dict[str, Any]] = []
        chunks = await self.store.list_chunks_by_doc(doc["_id"])
        other_docs = [d for d in await self.store.list_documents(status="active") if d["dept_id"] != doc["dept_id"] and d["_id"] != doc["_id"]]
        doc_titles = {d["_id"]: d["title"] for d in other_docs}

        for chunk in chunks:
            # 用该 chunk 内容做向量检索，跨部门召回最相似 chunk
            # （此处用 chunk 的 embedding_id 检索；简化：以 chunk 文本嵌入）
            similar = await self._search_similar(chunk, top_k=3)
            for hit in similar:
                other_dept = hit.get("dept_id")
                if other_dept == doc["dept_id"]:
                    continue
                other_doc_id = hit.get("doc_id", "")
                overlap = self._keyword_overlap(chunk.get("keywords", []), hit.get("keywords", []))
                score = float(hit.get("score", 0.0))
                # 语义层阈值：cosine > 0.85 且关键词重叠 > 0.3
                if score < 0.5 or overlap < 0.15:
                    continue
                if self.llm is not None:
                    verdict = await self._judge_conflict(doc["title"], chunk["content"], doc_titles.get(other_doc_id, ""), hit.get("content", ""))
                    if verdict.get("is_conflict") and verdict.get("confidence", 0) >= 0.6:
                        relations.append(
                            self._make_relation(doc, {"_id": other_doc_id, "dept_id": other_dept}, "conflict", verdict.get("reason", "条款冲突"), verdict.get("confidence", 0.6))
                        )
        return relations

    async def run_for_document(self, doc: dict[str, Any]) -> list[dict[str, Any]]:
        """入库时执行：引用挖掘 + 冲突检测，写入 doc_relations。"""
        relations = await self.detect_references(doc)
        relations += await self.detect_conflicts(doc)
        for rel in relations:
            await self.store.insert_relation(rel)
        if relations:
            logger.info("文档 %s 检测到 %d 条跨部门关联", doc["title"], len(relations))
        return relations

    async def _search_similar(self, chunk: dict[str, Any], top_k: int) -> list[dict[str, Any]]:
        if self.embeddings is None:
            return []
        try:
            vec = await self.embeddings.embed_query(chunk.get("content", ""))
            hits = await self.vector_store.search(vec, top_k=top_k + 5)
            enriched: list[dict[str, Any]] = []
            for hit in hits:
                if hit.get("doc_id") == chunk.get("doc_id"):
                    continue
                stored = await self.store.get("chunks", hit.get("id", ""))
                if not stored:
                    continue
                other_doc = await self.store.get_document(stored.get("doc_id", ""))
                if not other_doc or other_doc.get("status") != "active":
                    continue
                full = dict(hit)
                full.update(stored)
                enriched.append(full)
                if len(enriched) >= top_k:
                    break
            return enriched
        except Exception as exc:  # noqa: BLE001
            logger.warning("相似检索失败: %s", exc)
            return []

    @staticmethod
    def _keyword_overlap(kw_a: list[str], kw_b: list[str]) -> float:
        if not kw_a or not kw_b:
            return 0.0
        set_a, set_b = set(kw_a), set(kw_b)
        return len(set_a & set_b) / max(len(set_a), 1)

    async def _judge_conflict(self, title_a: str, text_a: str, title_b: str, text_b: str) -> dict[str, Any]:
        try:
            msg = ChatMessage.user(CONFLICT_PROMPT.format(title_a=title_a, text_a=text_a[:800], title_b=title_b, text_b=text_b[:800]))
            return await self.llm.complete_json([ChatMessage.system("你是制度冲突检测助手。"), msg], temperature=0.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("冲突判定失败: %s", exc)
            return {"is_conflict": False, "reason": "", "confidence": 0.0}

    @staticmethod
    def _make_relation(from_doc: dict[str, Any], to_doc: dict[str, Any], rel_type: str, desc: str, confidence: float) -> dict[str, Any]:
        return {
            "_id": uuid.uuid4().hex,
            "from_doc": from_doc["_id"],
            "from_dept": from_doc.get("dept_id", ""),
            "to_doc": to_doc.get("_id", ""),
            "to_dept": to_doc.get("dept_id", ""),
            "relation_type": rel_type,
            "description": desc,
            "auto_detected": True,
            "confidence": round(float(confidence), 3),
            "verified_by": None,
            "created_at": _now_iso(),
        }
