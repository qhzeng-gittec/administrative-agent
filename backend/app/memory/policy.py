"""记忆治理策略：权威等级、敏感字段、部门范围与时效校验。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

AUTHORITY_WEIGHT = {
    "official_document": 1.0,
    "admin_approved": 0.9,
    "explicit_user": 0.8,
    "conversation_summary": 0.65,
    "inferred": 0.4,
}

SENSITIVE_KEYS = {
    "id_card", "身份证", "password", "密码", "health", "健康",
    "psychological_result", "心理测评结果", "discipline", "处分",
    "bank_account", "银行卡", "financial_detail", "财务明细",
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class MemoryPolicy:
    @staticmethod
    def is_sensitive(key: str, value: Any) -> bool:
        text = f"{key} {value}".lower()
        return any(token.lower() in text for token in SENSITIVE_KEYS)

    @staticmethod
    def user_item_readable(item: dict[str, Any], user_id: str) -> bool:
        if item.get("user_id") != user_id or item.get("status") != "active":
            return False
        expires = parse_time(item.get("expires_at"))
        return expires is None or expires > now()

    @staticmethod
    def org_item_readable(
        item: dict[str, Any], dept_ids: list[str], role: str = "student"
    ) -> bool:
        if item.get("status") != "active" or item.get("review_status") != "approved":
            return False
        if item.get("scope") == "department" and item.get("dept_id") not in set(dept_ids):
            return False
        allowed = item.get("access_scope") or []
        if allowed and role not in allowed:
            return False
        current = now()
        effective_from = parse_time(item.get("effective_from"))
        effective_to = parse_time(item.get("effective_to"))
        expires = parse_time(item.get("expires_at"))
        return not (
            (effective_from and effective_from > current)
            or (effective_to and effective_to <= current)
            or (expires and expires <= current)
        )

    @staticmethod
    def authority(item: dict[str, Any]) -> float:
        return float(item.get("confidence", 1.0)) * AUTHORITY_WEIGHT.get(item.get("authority", "inferred"), 0.4)
