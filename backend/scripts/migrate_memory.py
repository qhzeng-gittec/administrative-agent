"""将旧四层大文档记忆迁移到事实平面 + 五个记忆平面。

迁移是幂等的：旧用户偏好转成显式语义记忆；无来源旧 FAQ 不发布，进入候选审核；
原始 history_queries/feedback_history 从长期画像移除。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from app.config import get_settings
from app.deps import build_container


async def migrate(container) -> dict[str, int]:
    report = {"user_items": 0, "faq_candidates": 0, "profiles_cleaned": 0}
    for profile in await container.store.find("user_profiles"):
        user_id = profile.get("_id", "")
        for key, value in (profile.get("prefs") or {}).items():
            if key == "intent_counts":
                continue
            existing = await container.store.find("user_memory_items", {"user_id": user_id, "key": key, "status": "active"})
            if not existing:
                try:
                    await container.user_semantic_memory.remember(
                        user_id, key, value, source_type="explicit_user", actor_id="memory_migration"
                    )
                    report["user_items"] += 1
                except ValueError:
                    pass
        if "history_queries" in profile or "feedback_history" in profile:
            profile.pop("history_queries", None)
            profile.pop("feedback_history", None)
            await container.store.upsert_user_profile(profile)
            report["profiles_cleaned"] += 1

    for legacy in await container.store.find("dept_memory"):
        for faq in legacy.get("faqs") or []:
            candidate_id = "mcand_faq_" + str(faq.get("_id") or uuid.uuid4().hex)
            if await container.store.get("memory_candidates", candidate_id):
                continue
            await container.store.upsert("memory_candidates", {
                "_id": candidate_id, "kind": "organization_faq", "dept_id": legacy.get("dept_id"),
                "title": faq.get("question", ""), "content": faq.get("answer", ""),
                "reason": "legacy_faq_missing_source_refs", "status": "pending",
                "created_at": datetime.now(timezone.utc),
            })
            report["faq_candidates"] += 1
    return report


async def main() -> None:
    container = build_container(get_settings())
    if container.mongo is not None:
        await container.mongo.connect()
    if hasattr(container.session_store, "connect"):
        await container.session_store.connect()
    print(await migrate(container))


if __name__ == "__main__":
    asyncio.run(main())
