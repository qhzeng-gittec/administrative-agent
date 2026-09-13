"""向量存储：内存实现（开发零运维）+ Chroma（可选）。生产可切 Milvus。

接口统一：add / search / delete_by_doc。
"""
from __future__ import annotations

import math
from typing import Any, Optional

from app.utils.logging import get_logger

logger = get_logger(__name__)


class VectorStore:
    """向量存储接口。"""

    async def add(self, id_: str, vector: list[float], metadata: dict[str, Any]) -> None: ...
    async def search(self, vector: list[float], top_k: int = 10, dept_id: Optional[str] = None) -> list[dict[str, Any]]: ...
    async def delete_by_doc(self, doc_id: str) -> None: ...
    async def count(self) -> int: ...


class MemoryVectorStore(VectorStore):
    """内存向量库：cosine 相似度（向量需已归一化；未归一化时自动归一）。"""

    def __init__(self) -> None:
        self._vecs: dict[str, list[float]] = {}
        self._meta: dict[str, dict[str, Any]] = {}

    async def add(self, id_: str, vector: list[float], metadata: dict[str, Any]) -> None:
        self._vecs[id_] = self._normalize(vector)
        self._meta[id_] = dict(metadata)

    async def search(self, vector: list[float], top_k: int = 10, dept_id: Optional[str] = None) -> list[dict[str, Any]]:
        q = self._normalize(vector)
        scores: list[tuple[float, str]] = []
        for id_, v in self._vecs.items():
            if dept_id and self._meta[id_].get("dept_id") != dept_id:
                continue
            scores.append((self._dot(q, v), id_))
        scores.sort(key=lambda x: x[0], reverse=True)
        out = []
        for score, id_ in scores[:top_k]:
            out.append({"id": id_, "score": float(score), **self._meta[id_]})
        return out

    async def delete_by_doc(self, doc_id: str) -> None:
        for id_ in [i for i, m in self._meta.items() if m.get("doc_id") == doc_id]:
            self._vecs.pop(id_, None)
            self._meta.pop(id_, None)

    async def count(self) -> int:
        return len(self._vecs)

    @staticmethod
    def _normalize(v: list[float]) -> list[float]:
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    @staticmethod
    def _dot(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b))


class ChromaVectorStore(VectorStore):
    """Chroma 实现（可选，需 chromadb）。"""

    def __init__(self, collection_name: str = "wenshu_chunks") -> None:
        import chromadb  # 延迟导入

        self._client = chromadb.PersistentClient(path="./chroma_data")
        self._coll = self._client.get_or_create_collection(name=collection_name, metadata={"hnsw:space": "cosine"})

    async def add(self, id_: str, vector: list[float], metadata: dict[str, Any]) -> None:
        self._coll.upsert(ids=[id_], embeddings=[vector], metadatas=[metadata])

    async def search(self, vector: list[float], top_k: int = 10, dept_id: Optional[str] = None) -> list[dict[str, Any]]:
        where = {"dept_id": dept_id} if dept_id else None
        res = self._coll.query(query_embeddings=[vector], n_results=top_k, where=where)
        out = []
        ids = res.get("ids", [[]])[0]
        dists = res.get("distances", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        for id_, dist, meta in zip(ids, dists, metas):
            out.append({"id": id_, "score": float(1.0 - dist), **(meta or {})})
        return out

    async def delete_by_doc(self, doc_id: str) -> None:
        self._coll.delete(where={"doc_id": doc_id})

    async def count(self) -> int:
        return self._coll.count()


class MongoVectorStore(VectorStore):
    """MongoDB 共享向量存储。

    适用于当前 MVP/中小数据量，所有部门 Pod 共享同一份向量与版本状态；
    超过百万 chunk 时可无缝替换为 Milvus 实现。
    """

    def __init__(self, store) -> None:
        self.store = store

    async def add(self, id_: str, vector: list[float], metadata: dict[str, Any]) -> None:
        await self.store.upsert("vector_embeddings", {
            "_id": id_, "vector": self._normalize(vector), **metadata,
        })

    async def search(self, vector: list[float], top_k: int = 10, dept_id: Optional[str] = None) -> list[dict[str, Any]]:
        query = {"dept_id": dept_id} if dept_id else None
        rows = await self.store.find("vector_embeddings", query)
        normalized = self._normalize(vector)
        scored = [
            (self._dot(normalized, row.get("vector") or []), row) for row in rows
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {"id": row["_id"], "score": float(score), **{k: v for k, v in row.items() if k not in {"_id", "vector"}}}
            for score, row in scored[:top_k]
        ]

    async def delete_by_doc(self, doc_id: str) -> None:
        for row in await self.store.find("vector_embeddings", {"doc_id": doc_id}):
            await self.store.delete("vector_embeddings", row["_id"])

    async def count(self) -> int:
        return await self.store.count("vector_embeddings")

    @staticmethod
    def _normalize(v: list[float]) -> list[float]:
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    @staticmethod
    def _dot(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b))


def build_vector_store(backend: str, store=None) -> VectorStore:
    if backend == "mongo" and store is not None:
        return MongoVectorStore(store)
    if backend == "chroma":
        try:
            return ChromaVectorStore()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Chroma 初始化失败(%s)，回退内存向量库", exc)
    return MemoryVectorStore()
