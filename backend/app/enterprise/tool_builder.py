"""Model-authored, read-only tool recipes; no arbitrary Python or network execution."""
from __future__ import annotations

import hashlib
import json
import copy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.llm.tools import function
from app.support.evolution import SkillDraft


class ReadStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    tool: Literal["read_task", "read_policy", "list_materials"]
    collect_pages: bool = False

    @model_validator(mode="after")
    def paging(self):
        if self.collect_pages and self.tool != "list_materials":
            raise ValueError("Only list_materials supports cursor pagination")
        return self


class ToolRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^generated_[a-z][a-z0-9_]{0,39}$")
    description: str = Field(min_length=10, max_length=600)
    domain: Literal["onboarding", "access", "expense"]
    steps: list[ReadStep] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def unique_steps(self):
        if len({s.name for s in self.steps}) != len(self.steps):
            raise ValueError("Duplicate result names")
        return self


def recipe_digest(recipe: dict) -> str:
    body = ToolRecipe.model_validate(recipe).model_dump()
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def register_recipe(recipe: dict, handlers: dict, audit: list[dict]):
    spec = ToolRecipe.model_validate(recipe)
    if spec.name in handlers:
        raise ValueError("Tool name already registered")
    version = recipe_digest(recipe)

    def execute():
        output = {}
        for step in spec.steps:
            cursor, visited, items = None, set(), []
            for _ in range(20 if step.collect_pages else 1):
                arguments = {"cursor": cursor} if step.tool == "list_materials" else {}
                try:
                    value = handlers[step.tool](**arguments)
                except (ValueError, TypeError) as exc:
                    audit.append({"recipe": version, "tool": step.tool, "arguments": arguments, "error": str(exc)})
                    raise
                # Preserve every primitive call, including pagination and failure evidence.
                audit.append({"recipe": version, "tool": step.tool, "arguments": arguments, "result": copy.deepcopy(value)})
                if not step.collect_pages:
                    output[step.name] = value
                    break
                items.extend(value["items"])
                cursor = value["next_cursor"]
                if cursor is None:
                    output[step.name] = {"items": items, "complete": True}
                    break
                if cursor in visited:
                    raise ValueError("Pagination cursor repeated; completeness unknown")
                visited.add(cursor)
            else:
                raise ValueError("Pagination limit exceeded; completeness unknown")
        return {"recipe_digest": version, "data": output, "simulation": True}

    return function(spec.name, spec.description, {}, []), execute


BUILD_PROMPT = """你是企业工具构建复盘 Agent。只分析给定的实际执行轨迹，用户不满不是错误证明。
选择 decision: no_change（没有证实共同问题），maintenance（底层接口故障或权限缺失），skill（现有工具够用，改步骤），tool（重复读取/分页可封装成可靠只读工具）。
不要绕过审批，不生成写入、授权、支付或外部联网能力。无需把每个问题变成工具。
仅返回 JSON：decision,reason,evidence_task_ids,skill,tool。
skill 非空时结构为 {name,domain,description,instructions}，其余情况 null。
tool 非空时结构为 {name,description,domain,steps}，其余情况 null。
工具 name 必须 generated_ 开头，每个 step 为 {name,tool,collect_pages}。
唯一允许的底层工具：read_task、read_policy、list_materials；只有 list_materials 能 collect_pages=true。
list_materials 返回 items,next_cursor，分页聚合由有界执行器处理，错误会向上传播而不会当空列表。
description 用中文说明用途及何时使用；结果不能伪造业务判断。最多4步。
至少引用两条实际任务 ID 的共同证据。未知材料状态不能判断为材料缺失，正常审批不等于错误。
不要包含任务特定员工名、答案或测试编号。
"""


class BuildDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["no_change", "maintenance", "skill", "tool"]
    reason: str = Field(min_length=1, max_length=3000)
    evidence_task_ids: list[str] = Field(max_length=40)
    skill: SkillDraft | None
    tool: ToolRecipe | None


async def propose(llm, tasks: list[dict], group: dict) -> dict:
    selected = [t for t in tasks if t["id"] in group["task_ids"]]
    # No gold, scoring checks, or experiment-only defect labels enter the proposer.
    payload = []
    for task in selected:
        business_input = copy.deepcopy(task["input"])
        environment = business_input.get("environment", {})
        for key in ("tool_failure", "material_api_failure", "material_page_size"):
            environment.pop(key, None)
        payload.append({"id": task["id"], "input": business_input, "run": task["run"]})
    messages = [
        {"role": "system", "content": BUILD_PROMPT},
        {"role": "user", "content": json.dumps({"tasks": payload, "response_schema": BuildDecision.model_json_schema()}, ensure_ascii=False)},
    ]
    response = await llm.complete_json(messages)
    try:
        decision = BuildDecision.model_validate(response)
    except ValueError as exc:
        messages.extend([{"role": "assistant", "content": json.dumps(response, ensure_ascii=False)},
                         {"role": "user", "content": f"输出结构未通过给定schema：{exc}。只修正格式，不新增事实。"}])
        response = await llm.complete_json(messages)
        decision = BuildDecision.model_validate(response)
    known = {t["id"] for t in selected}
    if not set(decision.evidence_task_ids) <= known:
        raise ValueError("Review cites tasks outside the observed cohort")
    if decision.decision in {"skill", "tool"} and len(set(decision.evidence_task_ids)) < 2:
        raise ValueError("Candidate needs at least two observed tasks")
    if (decision.decision == "tool") != (decision.tool is not None):
        raise ValueError("Tool decision and body disagree")
    if (decision.decision == "skill") != (decision.skill is not None):
        raise ValueError("Skill decision and body disagree")
    if decision.tool and decision.tool.domain != group["domain"]:
        raise ValueError("Tool domain differs from cohort")
    if decision.skill:
        if decision.skill.domain != group["domain"]:
            raise ValueError("Skill domain differs from cohort")
    return decision.model_dump()


async def discover_capabilities(store, llm, settings) -> dict:
    """Background discovery produces candidates only; evaluation remains a separate gate."""
    from app.enterprise.fixtures import digest
    from app.enterprise.mining import cluster_tasks
    from app.support.evolution import now

    tasks = [t for t in store.records("task") if t.get("run")]
    if len(tasks) < settings.skill_min_cluster:
        return {"decision": "accumulating", "tasks": len(tasks), "clusters": [], "reflection_calls": 0}
    feedback = store.records("message", 5000)
    groups = await cluster_tasks(store, llm, settings, tasks, feedback)
    calls = 0
    for group in groups:
        if not group["eligible"]:
            continue
        members = [t for t in tasks if t["id"] in group["task_ids"]]
        key = digest({"tasks": members, "signals": group["signal_task_ids"], "prompt": BUILD_PROMPT, "model": llm.model})
        existing = store.get("capability_candidate", key)
        if existing:
            group.update(candidate_id=key, decision="already_processed")
            continue
        result = await propose(llm, members, group)
        row = {"id": key, "group": group, "decision": result, "status": "candidate" if result["decision"] in {"tool", "skill"} else result["decision"], "created_at": now()}
        if result["tool"]:
            row["digest"] = recipe_digest(result["tool"])
        store.put("capability_candidate", row)
        group.update(candidate_id=key, decision=row["status"])
        calls += 1
    return {"decision": "completed", "tasks": len(tasks), "clusters": groups, "reflection_calls": calls}
