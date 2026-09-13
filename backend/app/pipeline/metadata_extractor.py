"""元数据提取：LLM 抽取 effective_date / doc_type / keywords / applicable_scope / cross_refs。

同时提供 jieba 关键词抽取（供 BM25 / chunk keywords 使用）。
"""
from __future__ import annotations

from typing import Any

from app.llm.client import ChatMessage, LLMClient
from app.utils.logging import get_logger

logger = get_logger(__name__)

EXTRACT_PROMPT = """你是制度文档元数据抽取助手。请从给定文档文本中抽取元数据，仅输出 JSON，不要输出任何解释。

字段说明：
- effective_date: 生效日期（YYYY-MM-DD，无则 null）
- doc_type: 文档类型，取值之一 regulation(制度办法)/notice(通知)/guide(指南)/form(表格)/other
- keywords: 关键词列表（3-8 个中文短语）
- applicable_scope: 适用对象列表，取值集合子集：undergraduate(本科生)/graduate(研究生)/faculty(教职工)/staff(行政人员)/all(全体)
- cross_refs: 文中引用的其他制度文件名列表（无则 []）

JSON 结构：
{{"effective_date": "...", "doc_type": "...", "keywords": [...], "applicable_scope": [...], "cross_refs": [...]}}

文档标题：{title}

文档文本（节选）：
{text}
"""


class MetadataExtractor:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def extract(self, title: str, text: str) -> dict[str, Any]:
        text = text[:6000]  # 截断控制成本
        messages = [
            ChatMessage.system("你是严谨的制度文档元数据抽取助手。"),
            ChatMessage.user(EXTRACT_PROMPT.format(title=title, text=text)),
        ]
        try:
            data = await self.llm.complete_json(messages, temperature=0.0)
            return self._normalize(data)
        except Exception as exc:  # noqa: BLE001 - 元数据失败不阻断入库
            logger.warning("元数据抽取失败(%s)，使用默认值", exc)
            return {
                "effective_date": None,
                "doc_type": "other",
                "keywords": [],
                "applicable_scope": ["all"],
                "cross_refs": [],
            }

    @staticmethod
    def _normalize(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {"effective_date": None, "doc_type": "other", "keywords": [], "applicable_scope": ["all"], "cross_refs": []}
        allowed_types = {"regulation", "notice", "guide", "form", "other"}
        allowed_scope = {"undergraduate", "graduate", "faculty", "staff", "all"}
        return {
            "effective_date": data.get("effective_date") or None,
            "doc_type": data.get("doc_type") if data.get("doc_type") in allowed_types else "other",
            "keywords": [str(k) for k in (data.get("keywords") or [])][:8],
            "applicable_scope": [s for s in (data.get("applicable_scope") or []) if s in allowed_scope] or ["all"],
            "cross_refs": [str(r) for r in (data.get("cross_refs") or [])],
        }


def extract_keywords(text: str, top_k: int = 6) -> list[str]:
    """jieba TF-IDF 关键词抽取（纯本地，供 chunk keywords / BM25 增强）。"""
    try:
        import jieba.analyse  # 延迟导入

        return jieba.analyse.extract_tags(text, topK=top_k, withWeight=False)
    except Exception:  # noqa: BLE001
        return []
