"""Rule Engine：硬约束规则（优先级最高），运行时注入 + 进化更新。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.storage.store import DataStore
from app.utils.logging import get_logger

logger = get_logger(__name__)

# 内置全局规则（循环起点）
DEFAULT_RULES = [
    {
        "_id": "rule_cite_source",
        "name": "cite_source_rule",
        "scope": "global",
        "content": "所有回答必须附带来源条款引用。",
        "priority": 100,
        "status": "active",
        "auto_generated": False,
        "confidence": 1.0,
        "created_by": "system",
        "created_at": "",
    },
    {
        "_id": "rule_no_guess",
        "name": "no_guess_rule",
        "scope": "global",
        "content": "文档中没有明确答案时必须说'根据现有制度文件未找到明确规定'，禁止编造。",
        "priority": 100,
        "status": "active",
        "auto_generated": False,
        "confidence": 1.0,
        "created_by": "system",
        "created_at": "",
    },
]


class RuleEngine:
    def __init__(self, store: DataStore) -> None:
        self.store = store

    async def seed_defaults(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for rule in DEFAULT_RULES:
            r = dict(rule)
            r["created_at"] = r["created_at"] or now
            if await self.store.get("rules", r["_id"]) is None:
                await self.store.upsert_rule(r)

    async def active_rules(self, dept_ids: Optional[list[str]] = None) -> list[dict[str, Any]]:
        rules = await self.store.list_rules(status="active")
        if dept_ids:
            allowed = set(dept_ids)
            rules = [
                r for r in rules
                if r.get("scope") == "global"
                or (r.get("scope") == "department" and r.get("dept_id") in allowed)
            ]
        else:
            rules = [r for r in rules if r.get("scope") == "global"]
        rules.sort(key=lambda r: r.get("priority", 0), reverse=True)
        return rules

    def format_rules(self, rules: list[dict[str, Any]]) -> str:
        if not rules:
            return "- 必须引用来源\n- 无依据不编造"
        return "\n".join(f"- {r.get('content', '')}" for r in rules)

    async def register(self, rule: dict[str, Any], auto_activate: bool = False) -> None:
        rule["status"] = "active" if auto_activate else rule.get("status", "pending")
        rule.setdefault("auto_generated", True)
        rule.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        await self.store.upsert_rule(rule)

    async def approve(self, rule_id: str) -> None:
        rule = await self.store.get("rules", rule_id)
        if rule:
            rule["status"] = "active"
            await self.store.upsert_rule(rule)

    async def deprecate(self, rule_id: str) -> None:
        rule = await self.store.get("rules", rule_id)
        if rule:
            rule["status"] = "deprecated"
            await self.store.upsert_rule(rule)
