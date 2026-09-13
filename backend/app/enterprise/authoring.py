"""Bounded native-tool authoring over discovery evidence and validation feedback."""
import copy
import json

from app.enterprise.evolution import Proposal, REFLECT, build_proposal, proposal_context
from app.llm.tools import function


async def author_proposal(llm, store, cycle: dict, settings) -> dict:
    context = proposal_context(cycle, settings.evolution_generated_cases)
    tasks = {t["id"]: t for t in context["discovery"]}
    capabilities = {c["id"]: c for c in cycle["before"]}
    primitives = {t["function"]["name"]: t for t in context["builtin_tools"]}
    catalog = [{"id": c["id"], "kind": c["kind"], "name": c["body"]["name"],
                "selection": c["selection"]} for c in capabilities.values()]
    if "authoring" not in cycle:
        cycle["authoring"] = {"rounds": 0, "read_tasks": [], "read_capabilities": [], "messages": [
            {"role": "system", "content": REFLECT + "\n你可以多轮调用查阅工具。提交后系统执行验证；未提升可读取反馈再修订。"
             "首次提交同时生成评测计划，后续提交必须保持同一计划。所有修订相对当前有效目录before，不能把未发布候选当作已生效能力。"
             "不能访问留出集。预算不足或无可靠改进时用stop_review结束，不要重复提交相同候选。"},
            {"role": "user", "content": json.dumps({
                "discovery": [{"id": t["id"], "question": t["input"]["question"]} for t in tasks.values()],
                "feedback": context["feedback"], "capability_catalog": catalog,
                "builtin_tool_names": list(primitives), "case_count": settings.evolution_generated_cases,
                "max_candidates": settings.evolution_max_candidates,
            }, ensure_ascii=False)},
        ]}
    state = cycle["authoring"]

    def save():
        store.put("evolution_cycle", cycle)

    def list_capabilities(query: str = ""):
        return [c for c in catalog if query.casefold() in json.dumps(c, ensure_ascii=False).casefold()]

    def read_capability(capability_id: str):
        if capability_id not in capabilities:
            raise ValueError("能力不在当前同域有效目录中")
        if capability_id not in state["read_capabilities"]:
            state["read_capabilities"].append(capability_id)
        return capabilities[capability_id]

    def read_builtin_tool(name: str):
        if name not in primitives:
            raise ValueError("未知的底层工具")
        return primitives[name]

    def read_discovery_task(task_id: str):
        if task_id not in tasks:
            raise ValueError("只能读取发现分区中的任务")
        if task_id not in state["read_tasks"]:
            state["read_tasks"].append(task_id)
        return tasks[task_id]

    def read_validation(task_id: str | None = None):
        if not cycle.get("attempts"):
            raise ValueError("尚无已完成的候选验证")
        previous = cycle["attempts"][-1]
        rows = [r for r in store.records("evolution_pair", 10000)
                if r["cycle_id"] == cycle["id"] and r["stage"] == "validation"
                and r["proposal_digest"] == previous["proposal_digest"] and r["status"] == "completed"]
        if task_id is None:
            return {"summary": previous["validation"], "cases": [
                {k: r[k] for k in ("task_id", "baseline", "candidate", "candidate_used")} for r in rows]}
        row = next((r for r in rows if r["task_id"] == task_id), None)
        if row is None:
            raise ValueError("只能读取已完成的验证任务，不能读取留出集")
        source = next(t for t in cycle["partitions"]["validation"] + cycle["plan"]["cases"] if t["id"] == task_id)
        source = {"id": source["id"], "input": copy.deepcopy(source["input"]), "conversation": source.get("conversation", [])}
        for key in ("tool_failure", "material_api_failure", "material_page_size"):
            source["input"]["environment"].pop(key, None)
        return {"task": source, **{k: row[k] for k in ("baseline_run", "candidate_run", "baseline", "candidate")}}

    def submit_proposal(**data):
        proposal = Proposal.model_validate(data)
        references = set(proposal.change.evidence_ids)
        if proposal.evaluation:
            references.update(c.source_id for c in proposal.evaluation.cases)
        if not references <= set(state["read_tasks"]):
            raise ValueError("请先读取提案引用的任务证据")
        if not set(proposal.reuse_assessment.related_capability_ids) <= set(state["read_capabilities"]):
            raise ValueError("请先读取相关现有能力正文")
        result = build_proposal(data, cycle, settings.evolution_generated_cases, llm.model)
        state["result"] = result
        if proposal.evaluation and "evaluation" not in state:
            state["evaluation"] = proposal.evaluation.model_dump()
        return {"status": "submitted", "candidate_id": result["candidate_id"]}

    def stop_review(reason: str):
        if not reason.strip():
            raise ValueError("停止原因不能为空")
        state["result"] = {"stop_reason": reason}
        return {"status": "stopped", "reason": reason}

    handlers = {"list_capabilities": list_capabilities, "read_capability": read_capability,
                "read_builtin_tool": read_builtin_tool, "read_discovery_task": read_discovery_task,
                "read_validation": read_validation, "submit_proposal": submit_proposal, "stop_review": stop_review}
    tools = [
        function("list_capabilities", "查找当前同域有效Skill和组合工具的目录；query按名称和适用说明过滤。", {"query": {"type": "string"}}, []),
        function("read_capability", "读取现有Skill正文或组合工具步骤及版本，分析复用或修订。", {"capability_id": {"type": "string"}}, ["capability_id"]),
        function("read_builtin_tool", "查看业务执行器的底层工具schema；此操作不执行业务工具。", {"name": {"type": "string"}}, ["name"]),
        function("read_discovery_task", "读取发现集任务的政策、上下文与实际执行证据。", {"task_id": {"type": "string"}}, ["task_id"]),
        function("read_validation", "读取上一版验证汇总；指定task_id可查看前后轨迹和judge依据。", {"task_id": {"type": ["string", "null"]}}, []),
        {"type": "function", "function": {"name": "submit_proposal", "description": "提交候选与共同评测计划，系统随后运行验证。", "parameters": Proposal.model_json_schema()}},
        function("stop_review", "证据不足、无需变更或不值得继续时结束复盘，不发布候选。", {"reason": {"type": "string"}}, ["reason"]),
    ]
    assert set(handlers) == {t["function"]["name"] for t in tools}
    save()
    while True:
        messages = state["messages"]
        answered = {m["tool_call_id"] for m in messages if m["role"] == "tool"}
        assistant = next((m for m in reversed(messages) if m["role"] == "assistant"), {})
        pending = [c for c in assistant.get("tool_calls", []) if c["id"] not in answered]
        if not pending:
            if "result" in state:
                return state["result"]
            if state["rounds"] >= settings.evolution_author_rounds:
                state["result"] = {"stop_reason": "复盘模型调用预算耗尽"}
                save()
                return state["result"]
            turn = await llm.tool_turn(messages, tools)
            if turn["finish_reason"] not in {"stop", "tool_calls"}:
                raise ValueError("复盘模型未完成工具调用输出")
            pending = turn["message"].get("tool_calls") or []
            ids = [c["id"] for c in pending]
            previous_ids = {c["id"] for m in messages if m["role"] == "assistant" for c in m.get("tool_calls", [])}
            if len(ids) != len(set(ids)) or previous_ids.intersection(ids) or len(ids) > 8:
                raise ValueError("复盘工具调用ID重复或单轮超过8次调用")
            state["rounds"] += 1
            messages.append(turn["message"])
            if not pending:
                state["result"] = {"stop_reason": turn["message"].get("content") or "复盘未提交候选"}
            save()
        for call in pending:
            try:
                name = call["function"]["name"]
                if "result" in state:
                    raise ValueError("本次提案已结束，不能继续调用工具")
                if name not in handlers:
                    raise ValueError("不在复盘工具授权列表")
                result = handlers[name](**json.loads(call["function"]["arguments"]))
            except (ValueError, TypeError) as exc:
                result = {"error": str(exc)}
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, ensure_ascii=False)})
            save()
