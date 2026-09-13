import copy

import pytest

from app.enterprise.controlled_cases import controlled_dataset, learning_cases, score_controlled
from app.enterprise.tool_builder import ToolRecipe, register_recipe


def recipe():
    return {"name": "generated_material_inventory", "description": "完整汇总当前任务材料分页，保留未知与失败。", "domain": "expense",
            "steps": [{"name": "materials", "tool": "list_materials", "collect_pages": True}]}


def test_recipe_paginates_and_preserves_primitive_evidence():
    audit = []
    def page(cursor=None):
        return {"items": ["invoice" if cursor is None else "purpose"], "next_cursor": "next" if cursor is None else None}
    schema, run = register_recipe(recipe(), {"list_materials": page}, audit)
    assert schema["function"]["name"] == "generated_material_inventory"
    assert run()["data"]["materials"] == {"items": ["invoice", "purpose"], "complete": True}
    assert len(audit) == 2 and audit[1]["arguments"] == {"cursor": "next"}


def test_recipe_does_not_turn_outages_into_missing_materials():
    audit = []
    def page(cursor=None):
        if cursor:
            raise ValueError("Permission denied")
        return {"items": ["invoice"], "next_cursor": "second"}
    _, run = register_recipe(recipe(), {"list_materials": page}, audit)
    with pytest.raises(ValueError, match="Permission denied"):
        run()
    assert audit[-1]["error"] == "Permission denied"


def test_recipe_bounds_cursors_and_rejects_writes():
    _, run = register_recipe(recipe(), {"list_materials": lambda **_: {"items": [], "next_cursor": "same"}}, [])
    with pytest.raises(ValueError, match="repeated"):
        run()
    spec = recipe()
    spec["steps"][0]["tool"] = "create_request"
    with pytest.raises(ValueError):
        ToolRecipe.model_validate(spec)


def test_recipe_page_budget_is_finite():
    def page(cursor=None):
        return {"items": [], "next_cursor": str(int(cursor or 0)+1)}
    audit = []
    _, run = register_recipe(recipe(), {"list_materials": page}, audit)
    with pytest.raises(ValueError, match="limit exceeded"):
        run()
    assert len(audit) == 20


def test_recipe_trace_is_a_snapshot():
    row = {"requests": []}
    spec = recipe()
    spec["steps"] = [{"name": "task", "tool": "read_task", "collect_pages": False}]
    audit = []
    _, run = register_recipe(spec, {"read_task": lambda: row}, audit)
    run()
    row["requests"].append({"kind": "expense_review"})
    assert audit[0]["result"]["requests"] == []


@pytest.mark.asyncio
async def test_generated_tool_runs_in_native_loop_and_preserves_paging():
    import json
    from app.enterprise.agent import EnterpriseAgent
    case = next(r for r in controlled_dataset() if r["family"] == "expense-0")
    class Model:
        def __init__(self):
            self.step = 0
        async def tool_turn(self, messages, tools):
            if self.step == 0:
                calls = [("read_task", {}), ("read_policy", {}), ("generated_material_inventory", {})]
            else:
                result = json.loads(messages[-1]["content"])
                assert result["data"]["materials"]["items"] == case["task"]["input"]["environment"]["materials"]
                calls = [("finish_task", {"status": "needs_information", "missing_information": [], "explanation": "test complete"})]
            self.step += 1
            return {"message": {"role": "assistant", "content": None, "tool_calls": [{"id": f"{self.step}-{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]}, "finish_reason": "tool_calls", "usage": {}}
    run = await EnterpriseAgent(Model()).run(case["task"], tool_recipes=[recipe()])
    assert run["status"] == "completed"
    assert len(run["recipe_trace"]) >= 2


def test_controlled_data_has_unique_ids_changed_policy_and_hidden_gold():
    rows = controlled_dataset()
    assert len(rows) == 288 and len({r["id"] for r in rows}) == 288
    assert len(learning_cases(rows)) == 72
    assert all("gold" not in row["task"] and "defects" not in row["task"] for row in rows)
    assert len({r["split"] for r in rows}) == 3
    assert any("budget_owner" in r["task"]["input"]["environment"]["policy"]["content"] for r in rows if r["split"] == "test")


def test_scorer_rejects_mutated_requests_missing_fields_and_false_success():
    case = next(r for r in controlled_dataset() if r["family"] == "expense-1")
    gold = {"status": "completed", "result": {"status": "needs_information", "missing_information": ["business_purpose"]}, "requests": [],
            "tool_trace": [{"tool": "read_task"}, {"tool": "read_policy"}]}
    assert score_controlled(case, gold)["passed"]
    for mutation in [dict(result={"status": "completed", "missing_information": []}), dict(requests=[{"kind": "expense_review", "status": "waiting_approval"}]), dict(tool_trace=[])]:
        run = copy.deepcopy(gold)
        run.update(mutation)
        assert not score_controlled(case, run)["passed"]


@pytest.mark.asyncio
async def test_discovery_requires_cohort_and_deduplicates_without_gold(tmp_path):
    import json
    from app.config import Settings
    from app.enterprise.store import EnterpriseStore
    from app.enterprise.tool_builder import discover_capabilities
    class Model:
        model = "protocol-test"
        reviews = 0
        async def embed(self, texts, model):
            return [[1., 0.] for _ in texts]
        async def complete_json(self, messages):
            self.reviews += 1
            payload = json.loads(messages[1]["content"])["tasks"]
            assert all(set(t) == {"id", "input", "run"} for t in payload)
            assert all(not {"tool_failure", "material_api_failure", "material_page_size"} & set(t["input"]["environment"]) for t in payload)
            return {"decision": "no_change", "reason": "审批正常", "evidence_task_ids": [], "skill": None, "tool": None}
    store = EnterpriseStore(tmp_path/"state.sqlite")
    model = Model()
    assert (await discover_capabilities(store, model, Settings()))["reflection_calls"] == 0
    for i, row in enumerate([r for r in learning_cases(controlled_dataset()) if r["task"]["input"]["domain"] == "expense"]):
        task = {**row["task"], "run": {"result": {"status": "blocked"}}}
        store.put("task", task)
        if i < 3:
            store.put("message", {"id": str(i), "classification": {"signal": "possible_error", "task_id": task["id"]}})
    assert (await discover_capabilities(store, model, Settings()))["reflection_calls"] == 1
    assert (await discover_capabilities(store, model, Settings()))["reflection_calls"] == 0
    assert model.reviews == 1


@pytest.mark.asyncio
async def test_builder_skill_candidate_uses_existing_skill_contract():
    from app.enterprise.tool_builder import propose
    class Model:
        async def complete_json(self, messages):
            return {"decision": "skill", "reason": "两条实际记录遗漏材料核对", "evidence_task_ids": ["a", "b"], "tool": None,
                    "skill": {"name": "核对材料", "domain": "expense", "description": "提交前核对", "instructions": "先核对所需材料。"}}
    tasks = [{"id": i, "input": {}, "run": {}} for i in ["a", "b"]]
    result = await propose(Model(), tasks, {"task_ids": ["a", "b"], "domain": "expense"})
    assert result["skill"]["name"] == "核对材料"
