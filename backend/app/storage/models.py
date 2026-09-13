"""数据模型（对应 MongoDB 各集合，详见技术方案 3.1）。

统一用字符串 `_id` 简化序列化；时间统一 ISO8601 字符串。
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class MongoModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str = Field(alias="_id")

    def to_mongo(self) -> dict[str, Any]:
        data = self.model_dump(by_alias=True, exclude_none=True)
        return data


class Department(MongoModel):
    """部门元数据。"""

    name: str = ""
    name_en: str = ""
    category: str = "general"
    admin_users: list[str] = Field(default_factory=list)
    agent_config: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class Document(MongoModel):
    """文档主表。"""

    dept_id: str = ""
    title: str = ""
    doc_type: str = "regulation"
    version: str = ""
    status: str = "active"  # draft|review|active|archived|deleted
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    supersedes: Optional[str] = None
    source: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    chunk_count: int = 0
    vector_status: str = "pending"  # pending|ready|failed
    applicable_scope: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class Chunk(MongoModel):
    """切片表（检索最小单元）。"""

    doc_id: str = ""
    dept_id: str = ""
    chunk_index: int = 0
    section_path: list[str] = Field(default_factory=list)
    section_title: str = ""
    content: str = ""
    content_hash: str = ""
    char_count: int = 0
    embedding_id: str = ""
    keywords: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocRelation(MongoModel):
    """跨部门文件关联。"""

    from_doc: str = ""
    to_doc: str = ""
    relation_type: str = "reference"  # reference|supersede|conflict|supplement
    description: str = ""
    auto_detected: bool = True
    confidence: float = 0.0
    verified_by: Optional[str] = None


class Skill(MongoModel):
    """技能（Loop 沉淀的程序化知识）。"""

    name: str = ""
    dept_id: str = ""
    scope: str = "global"  # global|department
    trigger: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    version: int = 1
    status: str = "pending"  # pending|active|stale|deprecated
    auto_generated: bool = True
    confidence: float = 0.0
    created_by: str = "loop_engine"
    created_at: str = ""


class Hook(MongoModel):
    """钩子（事件响应）。"""

    name: str = ""
    dept_id: str = ""
    scope: str = "global"
    trigger: dict[str, Any] = Field(default_factory=dict)   # 触发条件
    action: dict[str, Any] = Field(default_factory=dict)    # 触发动作
    confidence: float = 0.0
    status: str = "pending"
    auto_generated: bool = True
    created_by: str = "loop_engine"
    created_at: str = ""


class Rule(MongoModel):
    """规则（硬约束，优先级最高）。"""

    name: str = ""
    scope: str = "global"
    content: str = ""
    priority: int = 100
    status: str = "pending"
    auto_generated: bool = True
    confidence: float = 0.0
    created_by: str = "loop_engine"
    created_at: str = ""


class FeedbackRecord(MongoModel):
    """反馈信号。"""

    session_id: str = ""
    user_id: str = ""
    query: str = ""
    answer: str = ""
    kind: str = "explicit"  # explicit|implicit|auto
    signal: str = "up"       # up|down|correction|follow_up|copy|verifier_pass|verifier_fail
    dept_ids: list[str] = Field(default_factory=list)
    intent_type: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    consumed: bool = False
    created_at: str = ""


class Trace(MongoModel):
    """一次问答的完整 trace（供 Loop Reflect 回放）。"""

    session_id: str = ""
    user_id: str = ""
    query: str = ""
    intent: dict[str, Any] = Field(default_factory=dict)
    retrieved_chunks: list[dict[str, Any]] = Field(default_factory=list)
    answer: str = ""
    citations: list[dict[str, Any]] = Field(default_factory=list)
    verification: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int = 0
    cost: float = 0.0
    success: bool = True
    created_at: str = ""


class GlossaryEntry(MongoModel):
    """术语映射（同义词）。"""

    canonical: str = ""
    synonyms: list[str] = Field(default_factory=list)
    dept_id: str = ""
    created_by: str = "llm"
    created_at: str = ""


class UserProfile(MongoModel):
    """用户画像。"""

    user_id: str = ""
    name: str = ""
    role: str = "student"  # student|teacher|admin
    department: str = ""
    grade: str = ""
    prefs: dict[str, Any] = Field(default_factory=dict)
    history_queries: list[str] = Field(default_factory=list)
    feedback_history: list[dict[str, Any]] = Field(default_factory=list)
    last_active: str = ""
    created_at: str = ""


class ConversationEvent(MongoModel):
    session_id: str = ""
    user_id: str = ""
    seq: int = 0
    type: str = "user_message"
    content: str = ""
    dept_ids: list[str] = Field(default_factory=list)
    trace_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: Any = None
    expires_at: Any = None


class ConversationSummary(MongoModel):
    session_id: str = ""
    user_id: str = ""
    summary: str = ""
    resolved_entities: dict[str, Any] = Field(default_factory=dict)
    unresolved_questions: list[str] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)
    updated_at: Any = None
    expires_at: Any = None


class UserMemoryItem(MongoModel):
    user_id: str = ""
    key: str = ""
    value: Any = None
    category: str = "preference"
    source_type: str = "explicit_user"
    source_event_id: str = ""
    authority: str = "explicit_user"
    confidence: float = 1.0
    consent: bool = True
    status: str = "active"
    revision: int = 1
    expires_at: Any = None


class OrganizationMemoryItem(MongoModel):
    scope: str = "department"
    dept_id: str = ""
    type: str = "faq"
    title: str = ""
    content: str = ""
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    source_doc_ids: list[str] = Field(default_factory=list)
    authority: str = "official_document"
    confidence: float = 1.0
    review_status: str = "pending"
    status: str = "active"
    access_scope: list[str] = Field(default_factory=list)
    revision: int = 1
    effective_from: Any = None
    effective_to: Any = None
    expires_at: Any = None
