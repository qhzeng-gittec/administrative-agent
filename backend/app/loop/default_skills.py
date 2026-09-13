"""Built-in baseline Skills used by the real runtime and the admin demo.

These are idempotent, executable workflow policies rather than display-only rows.
They provide a safe baseline before enough production traces exist for Skill Miner.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.storage.store import DataStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


DEFAULT_SKILLS: list[dict[str, Any]] = [
    {
        "_id": "skill_dept_hqaq_emergency_seed",
        "name": "极端天气安全响应",
        "description": "针对暴雨、台风和防汛问题扩大事实召回，并按风险、行动、求助方式组织答案。",
        "dept_id": "dept_hqaq", "scope": "department",
        "trigger": {
            "intent_patterns": ["暴雨", "台风", "防汛", "极端天气", "安全事项"],
            "entities_required": ["matter"], "confidence_threshold": 0.75,
        },
        "action": {
            "type": "workflow",
            "steps": [
                {"step": 1, "action": "extract_entity", "params": {"entity": "matter"}},
                {"step": 2, "action": "retrieve", "params": {"query": "{matter} 预警 避险 应急电话 校园安全", "top_k": 8}},
                {"step": 3, "action": "generate", "params": {"template": "风险-行动-求助清单"}},
            ],
        },
        "unique_rules": ["涉及应急电话、开放时间和响应等级时必须逐字核对来源，不得补全原文未提供的信息。"],
        "rubric_rules": ["答案至少覆盖出行、楼宇/实验室和遇险求助三个维度，并为关键行动附来源。"],
    },
    {
        "_id": "skill_dept_zfxy_procedure_seed",
        "name": "校园事项步骤导航",
        "description": "把心理测评、行李寄存等操作说明整理为有先后顺序、带条件和来源的办事步骤。",
        "dept_id": "dept_zfxy", "scope": "department",
        "trigger": {
            "intent_patterns": ["心理测评", "行李寄存", "操作说明", "怎么办理", "具体步骤"],
            "entities_required": ["matter"], "confidence_threshold": 0.72,
        },
        "action": {
            "type": "workflow",
            "steps": [
                {"step": 1, "action": "extract_entity", "params": {"entity": "matter"}},
                {"step": 2, "action": "retrieve", "params": {"query": "{matter} 登录 条件 步骤 提交 注意事项", "top_k": 7}},
                {"step": 3, "action": "generate", "params": {"template": "前置条件-办理步骤-完成确认"}},
            ],
        },
        "unique_rules": ["步骤顺序必须与原文一致；承诺书类问题要区分地点、期限、取用限制和责任主体。"],
        "rubric_rules": ["存在系统按钮或页面名称时保留原始名称，不能用模糊近义词替换。"],
    },
    {
        "_id": "skill_academic_deadline_seed",
        "name": "学术节点与截止日期核验",
        "description": "对开题、答辩、截止日期类问题同步扩大时间证据召回，并要求区分文件日期和事项日期。",
        "dept_id": "", "scope": "global",
        "trigger": {
            "intent_patterns": ["开题", "答辩", "截止", "什么时候", "最晚"],
            "entities_required": ["matter"], "confidence_threshold": 0.78,
        },
        "action": {
            "type": "workflow",
            "steps": [
                {"step": 1, "action": "retrieve", "params": {"query": "{matter} 通知 日期 时间 地点 截止", "top_k": 8}},
                {"step": 2, "action": "call_tool", "params": {"tool": "calendar_lookup"}},
                {"step": 3, "action": "generate", "params": {"template": "时间节点核验表"}},
            ],
        },
        "unique_rules": ["明确区分通知发布时间、材料提交截止时间、答辩时间和适用对象。"],
        "rubric_rules": ["制度与校历冲突时以当前 active 制度原文为事实依据，并提示人工确认。"],
    },
]


async def seed_default_skills(store: DataStore) -> int:
    created = 0
    for template in DEFAULT_SKILLS:
        if await store.get_skill(template["_id"]) is not None:
            continue
        skill = {
            **template, "metrics": {
                "trigger_count": 0, "success_count": 0, "success_rate": 0.0,
                "avg_latency_ms": 0, "last_triggered": "",
            },
            "version": 1, "status": "active", "auto_generated": False,
            "confidence": 1.0, "gray_percent": 1.0, "created_by": "system_seed",
            "origin": "builtin_baseline", "created_at": _now(),
        }
        await store.upsert_skill(skill)
        await store.upsert("strategy_versions", {
            "_id": f"strategy_version_{skill['_id']}_v1_seed",
            "artifact_id": skill["_id"], "artifact_type": "skill", "version": 1,
            "reason": "builtin_baseline_seeded", "snapshot": skill, "created_at": _now(),
        })
        created += 1
    return created
