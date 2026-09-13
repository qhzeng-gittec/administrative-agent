"""Offline protocol/lifecycle verification, not claims of real-model improvement."""
import copy
import json
import re
import sys

import httpx
import pytest

from app.config import Settings
from app.enterprise.assessment import judge_pair, summarize, execute
from app.enterprise.agent import builtin_tools
from app.enterprise.evolution import (active, advance, expire_trials, lifecycle_commit, observe_episode,
                                      observed_tasks, partition, proposal_context, build_proposal, run_cycle, select_catalog)
from app.enterprise.examples import examples
from app.enterprise.identity import digest
from app.enterprise.runtime import build_runtime
from app.enterprise.service import EnterpriseService
from app.enterprise.store import EnterpriseStore
from app.support.evolution import now


async def propose(model, cycle, count):
    # Exercise proposal validation directly; service cycles use the native-tool author.
    payload = proposal_context(cycle, count)
    response = await model.complete_json([{"role": "user", "content": json.dumps(payload)}])
    return build_proposal(response, cycle, count, model.model)


def settings(tmp_path):
    return Settings(_env_file=None, support_data_dir=str(tmp_path), openrouter_api_key="test", evolution_trial_samples=2,
                    evolution_gray_percent=1.0, evolution_generated_cases=2)


def task(key):
    value = copy.deepcopy(examples()[2]["input"])
    value["environment"]["materials"] = ["invoice", "itinerary", "business_purpose"]
    return {"id": key, "input": value, "conversation": []}


class Model:
    """Exercises real native-tool execution and structured model boundaries."""
    model = "offline-contract-double"

    def __init__(self, action="create", kind="skill", target=None, default_good=False):
        self.action, self.kind, self.target = action, kind, target
        self.default_good = default_good
        self.payloads = []
        self.calls = 0
        self.judge_fail = False
        self.judge_unknown = False
        self.judge_bad_path = False
        self.degrade = False
        self.revise_after_feedback = False
        self.author_messages = []

    async def embed(self, texts, model):
        return [[1.0, 0.0] for _ in texts]

    async def complete_json(self, messages, **kwargs):
        data = json.loads(messages[-1]["content"])
        self.payloads.append(data)
        if "discovery" in data:
            source = data["discovery"][0]
            evaluation = {"criteria": ["核对材料及政策", "实际申请与说明一致"], "cases": [{
                "source_id": source["id"], "purpose": "补充业务变体", "question": f"请检查费用材料，补充样例{i}",
                "employee": source["input"]["environment"]["employee"], "materials": ["invoice", "itinerary", "business_purpose"],
                "requests": []} for i in range(data["case_count"])]}
            body = {"name": "材料核对", "domain": "expense", "description": "核对材料并按政策办理", "instructions": "repair：完整核对后办理"}
            tool = {"name": "generated_context", "domain": "expense", "description": "读取任务和政策，适用于报销核对", "steps": [
                {"name": "task", "tool": "read_task", "collect_pages": False}, {"name": "policy", "tool": "read_policy", "collect_pages": False}]}
            mutation = self.action in {"create", "revise"}
            noop = self.action in {"no_change", "maintenance"}
            return {"reuse_assessment": {"related_capability_ids": [r["id"] for r in data["current_capabilities"]],
                                        "reason": "对照已有正文和实际使用记录，选择最小变更"},
                    "evaluation": None if noop else evaluation, "change": {"action": self.action, "reason": "多个任务没有按当前材料正确办理", "evidence_ids": [t["id"] for t in data["discovery"][:2]],
                    "target_id": self.target, "kind": None if noop else self.kind,
                    "selection": "费用核对时加载；repair" if self.action in {"create", "revise", "select"} else None,
                    "skill": body if mutation and self.kind == "skill" else None, "tool": tool if mutation and self.kind == "tool" else None}}
        if "criteria" in data and "a" in data:
            if self.judge_fail:
                raise ValueError("judge unavailable")
            result = {}
            for name in ("a", "b"):
                passed = bool(data[name]["requests"])
                result[name] = {"passed": None if self.judge_unknown else passed, "reason": "依据实际申请判断",
                                "evidence_paths": [f"{name}.missing" if self.judge_bad_path else f"{name}.requests"]}
            return result
        raise AssertionError(data.keys())

    async def tool_turn(self, messages, tools):
        if any(t["function"]["name"] == "submit_proposal" for t in tools):
            self.author_messages.append(copy.deepcopy(messages))
            initial = json.loads(messages[1]["content"])
            calls = [c for m in messages if m["role"] == "assistant" for c in m.get("tool_calls", [])]
            tool_results = {m["tool_call_id"]: json.loads(m["content"]) for m in messages if m["role"] == "tool"}
            feedback_index = next((i for i in range(len(messages)-1, 1, -1) if messages[i]["role"] == "user"), None)
            if not calls:
                actions = [("list_capabilities", {})] + [("read_discovery_task", {"task_id": t["id"]}) for t in initial["discovery"][:2]]
                actions += [("read_capability", {"capability_id": c["id"]}) for c in initial["capability_catalog"][:2]]
                actions += [("read_builtin_tool", {"name": "read_policy"})]
            elif feedback_index is not None and not self.revise_after_feedback:
                actions = [("stop_review", {"reason": "没有可靠的进一步改进"})]
            elif feedback_index is not None and not any(m["role"] == "tool" for m in messages[feedback_index+1:]):
                actions = [("read_validation", {})]
            elif feedback_index is not None and "cases" in json.loads(messages[-1]["content"]):
                actions = [("read_validation", {"task_id": json.loads(messages[-1]["content"])["cases"][0]["task_id"]})]
            else:
                discovery = [tool_results[c["id"]] for c in calls if c["function"]["name"] == "read_discovery_task"]
                existing = [tool_results[c["id"]] for c in calls if c["function"]["name"] == "read_capability"]
                payload = {"discovery": discovery, "current_capabilities": existing, "builtin_tools": builtin_tools(), "case_count": initial["case_count"]}
                response = await self.complete_json([{"role": "user", "content": json.dumps(payload)}])
                if feedback_index is not None:
                    response["evaluation"] = json.loads(messages[feedback_index]["content"])["frozen_evaluation"]
                actions = [("submit_proposal", response)]
            output = [{"id": f"author-{len(calls)+i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}} for i, (name,args) in enumerate(actions)]
            return {"message": {"role": "assistant", "content": None, "tool_calls": output}, "finish_reason": "tool_calls", "usage": {}}
        self.calls += 1
        payload = json.loads(messages[1]["content"])
        previous = [c for m in messages if m["role"] == "assistant" for c in m.get("tool_calls", [])]
        called = {c["function"]["name"] for c in previous}
        catalog = payload["skills"]
        recipes = [t["function"]["name"] for t in tools if t["function"]["name"].startswith("generated_")]
        if "read_task" not in called:
            actions = [("read_task", {}), ("read_policy", {})]
        elif catalog and "load_skill" not in called:
            actions = [("load_skill", {"skill_id": catalog[0]["id"]})]
        elif recipes and recipes[0] not in called:
            actions = [(recipes[0], {})]
        else:
            loaded_text = " ".join(m["content"] for m in messages if m["role"] == "tool")
            good = ("repair" in loaded_text or any("repair" in s["description"] for s in catalog) or bool(recipes) or self.default_good)
            if "harm" in loaded_text:
                good = False
            if self.degrade:
                good = not catalog and not recipes
            if good and "create_request" not in called:
                actions = [("create_request", {"kind": "expense_review"})]
            else:
                actions = [("finish_task", {"status": "waiting_approval" if good else "needs_information", "missing_information": [], "explanation": "等待审核" if good else "需要补充"})]
        calls = [{"id": f"call-{len(previous)+i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}} for i, (name, args) in enumerate(actions)]
        return {"message": {"role": "assistant", "content": None, "tool_calls": calls}, "finish_reason": "tool_calls", "usage": {}}


async def histories(store, model):
    # Fixed membership, chosen before results; enough observations in each partition.
    rows = [task(f"task-{i}") for i in range(80)]
    parts = partition(rows)
    chosen = parts["discovery"][:12] + parts["validation"][:4] + parts["holdout"][:4]
    for row in chosen:
        run = await execute(model, row, [])
        store.put("episode", {"id": f"episode-{row['id']}", "task_id": row["id"], "snapshot": row, "run": run,
            "origin": "workbench_execution", "assignment": None, "created_at": now()})
        if row in parts["discovery"][:3]:
            store.put("message", {"id": f"message-{row['id']}", "origin": "workbench_message", "text": "材料完整却说缺少",
                "classification": {"signal": "possible_error", "task_id": row["id"]}})
    return chosen


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["skill", "tool"])
async def test_internal_cycle_generates_evaluates_deploys_and_monitors(tmp_path, kind):
    config = settings(tmp_path)
    store = EnterpriseStore(tmp_path / "state.sqlite")
    model = Model(kind=kind)
    judge = Model()
    await histories(store, model)
    cycle = await run_cycle(store, model, judge, config)
    assert cycle["status"] == "canary"
    assert cycle["source"] == "internal_evolution"
    assert not active(store, "expense")
    assert cycle["frozen_at"] <= cycle["proposed_at"]
    assert len([p for p in model.payloads if "discovery" in p]) == 1
    assert not any("sources" in p for p in model.payloads)
    assert all("a" in p and "b" in p for p in judge.payloads)
    assert all(p["criteria"] == cycle["plan"]["criteria"] for p in judge.payloads)
    proposal_input = next(p for p in model.payloads if "discovery" in p)
    reserved = {t["id"] for name in ("validation", "holdout") for t in cycle["partitions"][name]}
    assert not reserved & {t["id"] for t in proposal_input["discovery"]}
    assert "gold" not in json.dumps(model.payloads, ensure_ascii=False)
    assert len(cycle["plan"]["cases"]) == 2
    for i in range(2):
        t = task(f"fresh-{i}")
        catalog, assignment = select_catalog(store, "expense", f"user-{i}")
        run = await execute(model, t, catalog)
        episode = {"task_id": t["id"], "snapshot": t, "run": run, "assignment": assignment}
        result = await observe_episode(store, model, model, config, episode)
    assert result["status"] == "active"
    assert active(store, "expense")[0]["kind"] == kind
    assert len(store.records("episode")) == 20  # Replay/generation did not become user history.
    assert select_catalog(store, "expense", "next")[1]["stage"] == "monitoring"
    lifecycle_commit(store, cycle["id"], "rolled_back")
    assert active(store, "expense") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["missing_plan", "foreign_source", "wrong_count", "changed_fields"])
async def test_combined_proposal_rejects_invalid_evaluation(defect):
    class InvalidModel(Model):
        async def complete_json(self, messages, **kwargs):
            value = await super().complete_json(messages, **kwargs)
            if defect == "missing_plan":
                value["evaluation"] = None
            elif defect == "foreign_source":
                value["evaluation"]["cases"][0]["source_id"] = "held-out-task"
            elif defect == "wrong_count":
                value["evaluation"]["cases"].pop()
            else:
                value["evaluation"]["cases"][0]["employee"] = {"invented_field": "x"}
            return value

    cycle = {"id": "invalid", "domain": "expense", "before": [],
             "partitions": {"discovery": [task("d1"), task("d2")]}, "discovery_feedback": []}
    with pytest.raises(ValueError):
        await propose(InvalidModel(), cycle, 2)


def capability(kind="skill"):
    body = {"name": "旧策略", "domain": "expense", "description": "已有报销策略", "instructions": "旧方法"}
    if kind == "tool":
        body = {"name": "generated_old", "domain": "expense", "description": "已有只读组合查询工具", "steps": [{"name": "task", "tool": "read_task", "collect_pages": False}]}
    return {"id": "old", "domain": "expense", "kind": kind, "body": body, "selection": "旧说明", "version": 1,
            "digest": digest(body), "status": "active", "created_at": now()}


def test_active_catalog_does_not_lose_older_capabilities(tmp_path):
    store = EnterpriseStore(tmp_path / "state.sqlite")
    old = capability()
    store.put("capability", old)
    for i in range(501):
        store.put("capability", {**old, "id": f"other-{i}", "domain": "access"})
    store.put("capability", {**old, "id": "retired", "status": "retired"})
    assert active(store, "expense") == [old]
    assert select_catalog(store, "expense", "user")[0] == [old]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["skill", "tool"])
async def test_proposal_reads_existing_body_and_can_only_change_selection(kind):
    old = capability(kind)
    model = Model(action="select", kind=kind, target=old["id"])
    discovery = [task("d1"), task("d2")]
    discovery[0]["run"] = {"loaded_skills": [], "tool_trace": []}
    cycle = {"id": "selection", "domain": "expense", "before": [old],
             "partitions": {"discovery": discovery}, "discovery_feedback": []}
    result = await propose(model, cycle, 2)
    payload = model.payloads[0]
    assert payload["current_capabilities"] == [old]
    assert payload["builtin_tools"] == builtin_tools()
    assert {r["function"]["name"] for r in payload["builtin_tools"]} == {
        "read_task", "read_policy", "create_request", "load_skill", "finish_task", "list_materials"}
    assert payload["discovery"][0]["run"] == discovery[0]["run"]
    assert result["after"][0]["body"] == old["body"]
    assert result["after"][0]["replaces"] == old["id"]
    assert result["reuse_assessment"]["related_capability_ids"] == [old["id"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["skill", "tool"])
async def test_renaming_existing_content_cannot_create_duplicate(kind):
    old = capability(kind)
    class DuplicateModel(Model):
        async def complete_json(self, messages, **kwargs):
            response = await super().complete_json(messages, **kwargs)
            response["change"][kind] = {**old["body"], "name": "generated_renamed" if kind == "tool" else "改名策略",
                                        "description": "相同功能换了新的描述"}
            return response
    cycle = {"id": "duplicate", "domain": "expense", "before": [old],
             "partitions": {"discovery": [task("d1"), task("d2")]}, "discovery_feedback": []}
    with pytest.raises(ValueError, match="换名新增"):
        await propose(DuplicateModel(kind=kind), cycle, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("reviewed", [[], ["other-domain-capability"]])
async def test_selection_requires_review_of_current_target(reviewed):
    class InvalidReview(Model):
        async def complete_json(self, messages, **kwargs):
            response = await super().complete_json(messages, **kwargs)
            response["reuse_assessment"]["related_capability_ids"] = reviewed
            return response
    cycle = {"id": "review", "domain": "expense", "before": [capability()],
             "partitions": {"discovery": [task("d1"), task("d2")]}, "discovery_feedback": []}
    with pytest.raises(ValueError, match="复用分析"):
        await propose(InvalidReview(action="select", target="old"), cycle, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["revise", "select", "retire", "no_change", "maintenance"])
async def test_existing_capability_operations_are_explicit_and_reversible(tmp_path, action):
    store = EnterpriseStore(tmp_path / "state.sqlite")
    old = capability()
    store.put("capability", old)
    cycle = {"id": f"cycle-{action}", "domain": "expense", "before": [old], "partitions": {"discovery": [task("d1"), task("d2")]},
             "discovery_feedback": [], "history": [], "status": "canary"}
    result = await propose(Model(action, target="old" if action not in {"no_change", "maintenance"} else None), cycle, 2)
    cycle.update(result)
    store.put("evolution_cycle", cycle)
    assert active(store, "expense") == [old]
    if action in {"no_change", "maintenance"}:
        assert result["after"] == [old]
        return
    lifecycle_commit(store, cycle["id"], "active")
    if action == "retire":
        assert not active(store, "expense")
    else:
        changed = active(store, "expense")[0]
        assert changed["version"] == 2 and changed["replaces"] == "old"
        if action == "select":
            assert changed["body"] == old["body"] and changed["selection"] != old["selection"]
    lifecycle_commit(store, cycle["id"], "rolled_back")
    assert active(store, "expense") == [old]


def test_partition_never_moves_old_tasks_when_history_grows():
    first = partition([task(str(i)) for i in range(30)])
    second = partition([task(str(i)) for i in range(100)])
    for name in first:
        assert {t["id"] for t in first[name]} <= {t["id"] for t in second[name]}


@pytest.mark.asyncio
async def test_fixture_and_generated_records_cannot_trigger_internal_evolution(tmp_path):
    store = EnterpriseStore(tmp_path / "state.sqlite")
    for i in range(30):
        store.put("task", {**task(str(i)), "run": {}, "synthetic": True})
        store.put("episode", {"id": str(i), "origin": "model_generated"})
    model = Model()
    assert (await run_cycle(store, model, model, settings(tmp_path)))["status"] == "accumulating"
    assert model.payloads == []


@pytest.mark.asyncio
async def test_judge_failure_resumes_same_outputs_and_frozen_candidate(tmp_path):
    store = EnterpriseStore(tmp_path / "state.sqlite")
    model = Model()
    config = settings(tmp_path)
    await histories(store, model)
    model.judge_fail = True
    with pytest.raises(ValueError, match="unavailable"):
        await run_cycle(store, model, model, config)
    frozen = store.records("evolution_cycle")[0]
    raw = store.records("evolution_pair")[0]
    assert raw["status"] == "running" and "candidate_run" in raw
    proposal_calls = len([p for p in model.payloads if "discovery" in p])
    model.judge_fail = False
    result = await run_cycle(store, model, model, config, retry_id=frozen["id"])
    assert result["status"] == "canary"
    assert result["proposal_digest"] == frozen["proposal_digest"]
    assert len([p for p in model.payloads if "discovery" in p]) == proposal_calls == 1
    assert result["evaluation_digest"] == frozen["evaluation_digest"]
    assert store.get("evolution_pair", raw["id"])["candidate_run"] == raw["candidate_run"]


@pytest.mark.asyncio
async def test_judge_unknown_is_not_a_win_and_fabricated_evidence_fails(tmp_path):
    model = Model()
    t = task("j")
    run = await execute(model, t, [])
    model.judge_unknown = True
    verdict = await judge_pair(model, t, run, run, ["检查"], "key")
    assert verdict["baseline"]["passed"] is None
    assert not summarize([verdict], 1)["passed"]
    model.judge_bad_path = True
    with pytest.raises(ValueError, match="不存在"):
        await judge_pair(model, t, run, run, ["检查"], "key")


@pytest.mark.asyncio
async def test_incomplete_execution_overrides_judge_success(tmp_path):
    model = Model(default_good=True)
    t = task("j")
    good = await execute(model, t, [])
    bad = copy.deepcopy(good)
    bad.update(status="budget_exhausted", result=None)
    verdict = await judge_pair(model, t, good, bad, ["检查"], "key")
    assert verdict["candidate"]["judge_passed"] is True
    assert verdict["candidate"]["passed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("passed", [True, False, None])
async def test_judge_business_verdict_does_not_require_administrative_fields(passed):
    class ArtifactJudge:
        model = "artifact-judge-double"

        async def complete_json(self, messages, **kwargs):
            payload = json.loads(messages[-1]["content"])
            assert payload["criteria"] == ["交付符合原始需求"]
            assert payload["task"]["input"]["environment"] == {"brief": "生成示意图"}
            return {label: {"passed": passed, "reason": "依据交付物与任务要求判断",
                            "evidence_paths": [f"{label}.result"]} for label in ("a", "b")}

    source = {"id": "artifact", "input": {"question": "生成示意图", "environment": {"brief": "生成示意图"}}}
    run = {"status": "completed", "result": {"artifact": "diagram.svg"}, "tool_trace": []}
    verdict = await judge_pair(ArtifactJudge(), source, run, run, ["交付符合原始需求"], "artifact")
    assert verdict["candidate"]["passed"] is passed
    assert verdict["candidate"]["checks"] == {"finished": True}


@pytest.mark.asyncio
async def test_expired_trial_preserves_baseline_and_does_not_publish(tmp_path):
    store = EnterpriseStore(tmp_path / "state.sqlite")
    model = Model()
    await histories(store, model)
    cycle = await run_cycle(store, model, model, settings(tmp_path))
    cycle["canary_started_at"] = "2020-01-01T00:00:00+00:00"
    store.put("evolution_cycle", cycle)
    expire_trials(store)
    assert store.get("evolution_cycle", cycle["id"])["status"] == "inconclusive"
    assert not active(store, "expense")


@pytest.mark.asyncio
async def test_trial_duplicate_tasks_and_training_tasks_do_not_count(tmp_path):
    store = EnterpriseStore(tmp_path / "state.sqlite")
    model = Model()
    await histories(store, model)
    config = settings(tmp_path)
    cycle = await run_cycle(store, model, model, config)
    catalog, assignment = select_catalog(store, "expense", "user")
    old = cycle["partitions"]["discovery"][0]
    result = await observe_episode(store, model, model, config, {"task_id": old["id"], "snapshot": old, "run": old["run"], "assignment": assignment})
    assert result["status"] == "excluded"
    t = task("fresh")
    episode = {"task_id": t["id"], "snapshot": t, "run": await execute(model, t, catalog), "assignment": assignment}
    await observe_episode(store, model, model, config, episode)
    await observe_episode(store, model, model, config, episode)
    assert store.get("evolution_cycle", cycle["id"])["canary_summary"]["samples"] == 1


@pytest.mark.asyncio
async def test_runtime_auth_api_and_no_school_or_external_benchmark_routes(tmp_path):
    from fastapi import FastAPI
    from app.api.router import api_router, root_router
    app = FastAPI()
    app.state.container = await build_runtime(settings(tmp_path))
    app.include_router(api_router)
    app.include_router(root_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert response.status_code == 200
        headers = {"Authorization": "Bearer " + response.json()["data"]["token"]}
        assert (await client.get("/readyz")).json()["runtime"] == "enterprise"
        assert (await client.get("/api/v1/enterprise/evolution", headers=headers)).json()["data"]["cycles"] == []
        for path in ("/api/v1/chat", "/api/v1/admin", "/api/v1/enterprise/experiments", "/api/v1/enterprise/demo-history"):
            assert (await client.get(path, headers=headers)).status_code == 404
        created = await client.post("/api/v1/enterprise/tasks", json=task("live")["input"], headers=headers)
        assert created.status_code == 200
        assert created.json()["data"]["task"]["origin"] == "workbench_task"


@pytest.mark.asyncio
async def test_service_uses_catalog_and_enqueues_automatic_observation(tmp_path, monkeypatch):
    config = settings(tmp_path)
    svc = EnterpriseService(config)
    model = Model()
    await histories(svc.store, model)
    cycle = await run_cycle(svc.store, model, model, config)
    monkeypatch.setattr("app.enterprise.service.OpenRouterClient", lambda *args: model)
    t = {**task("user-task"), "user_id": "u", "origin": "workbench_task"}
    svc.store.put("task", t)
    job = svc.store.enqueue("execute", {"task_id": t["id"], "text": t["input"]["question"]})
    await svc.process(job)
    saved = svc.store.get("task", t["id"])
    assert saved["run"]["loaded_skills"] == [cycle["candidate_id"]]
    assert svc.store.get("episode", job["id"])["origin"] == "workbench_execution"
    observation = next(j for j in svc.store.records("job") if j["type"] == "observe")
    assert (await svc.process(observation))["status"] == "canary"


@pytest.mark.asyncio
async def test_complaint_uses_original_attempt_snapshot(tmp_path):
    store = EnterpriseStore(tmp_path / "state.sqlite")
    model = Model()
    t = task("task")
    first = await execute(model, t, [])
    for ident, run in (("old", first), ("new", {**first, "result": {"explanation": "changed"}})):
        store.put("episode", {"id": ident, "task_id": t["id"], "snapshot": t, "run": run, "origin": "workbench_execution"})
    store.put("message", {"id": "feedback", "origin": "workbench_message", "classification": {"task_id": "task"}, "reported_attempt": {"run": {"episode_id": "old"}}})
    assert observed_tasks(store)[0]["episode_id"] == "old"


@pytest.mark.asyncio
async def test_enabled_capability_automatically_rolls_back_on_fresh_degradation(tmp_path):
    config = settings(tmp_path)
    store = EnterpriseStore(tmp_path / "state.sqlite")
    model = Model()
    await histories(store, model)
    cycle = await run_cycle(store, model, model, config)
    for i in range(2):
        catalog, assignment = select_catalog(store, "expense", f"subject-{i}")
        t = task(f"promote-{i}")
        await observe_episode(store, model, model, config, {"task_id": t["id"], "snapshot": t,
            "run": await execute(model, t, catalog), "assignment": assignment})
    assert store.get("evolution_cycle", cycle["id"])["status"] == "active"
    model.degrade = True
    for i in range(2):
        catalog, assignment = select_catalog(store, "expense", f"subject-{i+2}")
        t = task(f"degrade-{i}")
        result = await observe_episode(store, model, model, config, {"task_id": t["id"], "snapshot": t,
            "run": await execute(model, t, catalog), "assignment": assignment})
    assert result["status"] == "rolled_back"
    assert active(store, "expense") == []


@pytest.mark.asyncio
async def test_no_gain_candidate_rejected_without_trial(tmp_path):
    store = EnterpriseStore(tmp_path / "state.sqlite")
    model = Model(default_good=True)
    await histories(store, model)
    cycle = await run_cycle(store, model, model, settings(tmp_path))
    assert cycle["status"] == "rejected" and cycle["rejection_stage"] == "validation"
    assert not active(store, "expense")


@pytest.mark.asyncio
async def test_release_cannot_overwrite_a_newer_catalog(tmp_path):
    store = EnterpriseStore(tmp_path / "state.sqlite")
    model = Model()
    await histories(store, model)
    cycle = await run_cycle(store, model, model, settings(tmp_path))
    store.put("capability", capability())
    with pytest.raises(ValueError, match="目录已变化"):
        lifecycle_commit(store, cycle["id"], "active")
    assert active(store, "expense")[0]["id"] == "old"


@pytest.mark.asyncio
async def test_generated_suite_is_not_the_only_quality_gate(tmp_path):
    store = EnterpriseStore(tmp_path / "state.sqlite")
    model = Model()
    await histories(store, model)
    model.judge_unknown = True
    cycle = await run_cycle(store, model, model, settings(tmp_path))
    assert cycle["status"] == "rejected"
    assert "insufficient_judged_samples" in cycle["validation"]["reasons"]
    assert not active(store, "expense")


@pytest.mark.asyncio
async def test_finished_execution_retry_is_idempotent(tmp_path, monkeypatch):
    svc = EnterpriseService(settings(tmp_path))
    model = Model()
    monkeypatch.setattr("app.enterprise.service.OpenRouterClient", lambda *args: model)
    t = {**task("user-task"), "user_id": "u", "origin": "workbench_task"}
    svc.store.put("task", t)
    job = svc.store.enqueue("execute", {"task_id": t["id"], "text": "办理"})
    await svc.process(job)
    conversation = svc.store.get("task", t["id"])["conversation"]
    calls = model.calls
    retry = svc.store.enqueue("execute", {**job["payload"], "execution_id": job["id"]})
    assert (await svc.process(retry))["recovered"]
    assert model.calls == calls and len(svc.store.records("episode")) == 1
    assert svc.store.get("task", t["id"])["conversation"] == conversation


@pytest.mark.asyncio
async def test_classification_retry_does_not_consume_removed_snapshot(tmp_path, monkeypatch):
    svc = EnterpriseService(settings(tmp_path))
    row = svc.add_message("u", "新需求")
    class Classifier:
        model = "classifier-double"
        async def complete_json(self, *args, **kwargs):
            return {"signal": "none", "task_id": None, "evidence": ""}
    monkeypatch.setattr("app.enterprise.service.OpenRouterClient", lambda *args: Classifier())
    job = {"type": "classify", "payload": {"message_id": row["id"]}}
    first = await svc.process(job)
    assert await svc.process(job) == first


@pytest.mark.asyncio
async def test_resume_cannot_skip_persisted_failed_gate(tmp_path):
    store = EnterpriseStore(tmp_path / "state.sqlite")
    model = Model(default_good=True)
    await histories(store, model)
    config = settings(tmp_path)
    cycle = await run_cycle(store, model, model, config)
    assert cycle["status"] == "rejected"
    cycle["status"] = "failed"
    store.put("evolution_cycle", cycle)
    result = await run_cycle(store, model, model, config, retry_id=cycle["id"])
    assert result["status"] == "rejected" and "holdout" not in result


@pytest.mark.asyncio
async def test_author_revises_after_validation_without_seeing_holdout(tmp_path):
    class RevisingModel(Model):
        async def complete_json(self, messages, **kwargs):
            result = await super().complete_json(messages, **kwargs)
            if "change" in result:
                attempts = sum("discovery" in p for p in self.payloads)
                result["change"]["skill"]["instructions"] = "尚未修正" if attempts == 1 else "repair：根据验证反馈修订"
                result["change"]["selection"] = "核对费用材料"
            return result

    model = RevisingModel()
    model.revise_after_feedback = True
    store = EnterpriseStore(tmp_path / "state.sqlite")
    config = settings(tmp_path)
    await histories(store, model)
    cycle = await run_cycle(store, model, Model(), config)
    assert cycle["status"] == "canary"
    assert len(cycle["attempts"]) == 2
    first, second = cycle["attempts"]
    assert not first["validation"]["passed"] and second["validation"]["passed"]
    assert first["candidate_id"] != second["candidate_id"]
    messages = cycle["authoring"]["messages"]
    calls = [c for m in messages if m["role"] == "assistant" for c in m.get("tool_calls", [])]
    assert any(c["function"]["name"] == "read_validation" and json.loads(c["function"]["arguments"]).get("task_id") for c in calls)
    assert {c["id"] for c in calls} == {m["tool_call_id"] for m in messages if m["role"] == "tool"}
    hidden = {t["id"] for t in cycle["partitions"]["holdout"]}
    assert not any(re.search(re.escape(task_id) + r"(?![a-zA-Z0-9_-])", json.dumps(messages)) for task_id in hidden)
    pairs = store.records("evolution_pair", 10000)
    assert {p["proposal_digest"] for p in pairs if p["stage"] == "holdout"} == {second["proposal_digest"]}
    for task_row in cycle["partitions"]["validation"]:
        versions = [p for p in pairs if p["stage"] == "validation" and p["task_id"] == task_row["id"]]
        assert len(versions) == 2
        assert versions[0]["baseline_run"] == versions[1]["baseline_run"]


@pytest.mark.asyncio
async def test_candidate_budget_stops_repeated_unhelpful_revisions(tmp_path):
    class NoGainModel(Model):
        async def complete_json(self, messages, **kwargs):
            response = await super().complete_json(messages, **kwargs)
            if "change" in response:
                response["change"]["skill"]["instructions"] += str(sum("discovery" in p for p in self.payloads))
            return response
    model = NoGainModel(default_good=True)
    model.revise_after_feedback = True
    store = EnterpriseStore(tmp_path / "state.sqlite")
    config = settings(tmp_path)
    config.evolution_max_candidates = 2
    await histories(store, model)
    cycle = await run_cycle(store, model, Model(), config)
    assert cycle["status"] == "rejected"
    assert len(cycle["attempts"]) == 2 and "holdout" not in cycle
    assert not active(store, "expense")


@pytest.mark.asyncio
async def test_holdout_failure_is_terminal_and_not_sent_to_author(tmp_path):
    model = Model()
    store = EnterpriseStore(tmp_path / "state.sqlite")
    await histories(store, model)
    held = {t["id"] for t in partition(observed_tasks(store))["holdout"]}
    class HoldoutJudge(Model):
        async def complete_json(self, messages, **kwargs):
            result = await super().complete_json(messages, **kwargs)
            if json.loads(messages[-1]["content"])["task"]["id"] in held:
                for grade in result.values():
                    grade["passed"] = False
            return result
    cycle = await run_cycle(store, model, HoldoutJudge(), settings(tmp_path))
    assert cycle["status"] == "rejected" and cycle["rejection_stage"] == "holdout"
    assert len(cycle["attempts"]) == 1
    assert not any(re.search(re.escape(task_id) + r"(?![a-zA-Z0-9_-])", json.dumps(cycle["authoring"]["messages"])) for task_id in held)


@pytest.mark.asyncio
async def test_revision_cannot_replace_frozen_evaluation():
    cycle = {"id": "freeze", "domain": "expense", "before": [],
             "partitions": {"discovery": [task("d1"), task("d2")]}, "discovery_feedback": []}
    original = await propose(Model(), cycle, 2)
    cycle["plan"] = original["plan"]
    class EasierCriteria(Model):
        async def complete_json(self, messages, **kwargs):
            result = await super().complete_json(messages, **kwargs)
            result["evaluation"]["criteria"] = ["只看文字是否流畅", "不核对真实申请"]
            return result
    with pytest.raises(ValueError, match="已冻结"):
        await propose(EasierCriteria(), cycle, 2)


@pytest.mark.asyncio
async def test_author_cannot_read_holdout_and_stops_at_round_budget(tmp_path):
    from app.enterprise.authoring import author_proposal
    class CuriousModel:
        model = "curious-double"
        async def tool_turn(self, messages, tools):
            return {"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "outside", "type": "function", "function": {"name": "read_discovery_task", "arguments": '{"task_id":"secret"}'}}]},
                "finish_reason": "tool_calls"}
    store = EnterpriseStore(tmp_path / "state.sqlite")
    config = settings(tmp_path)
    config.evolution_author_rounds = 1
    hidden = task("secret")
    hidden["input"]["question"] = "HOLDOUT_CONTENT_MUST_STAY_HIDDEN"
    cycle = {"id": "bounded", "domain": "expense", "before": [],
             "partitions": {"discovery": [task("d1"), task("d2")], "holdout": [hidden]}, "discovery_feedback": []}
    result = await author_proposal(CuriousModel(), store, cycle, config)
    assert "预算耗尽" in result["stop_reason"]
    assert "只能读取发现" in cycle["authoring"]["messages"][-1]["content"]
    assert "HOLDOUT_CONTENT_MUST_STAY_HIDDEN" not in json.dumps(cycle["authoring"]["messages"])


@pytest.mark.asyncio
async def test_author_transport_failure_resumes_after_completed_reads(tmp_path):
    class InterruptedAuthor(Model):
        fail_author = True
        async def tool_turn(self, messages, tools):
            if any(t["function"]["name"] == "submit_proposal" for t in tools) and len(messages) > 2 and self.fail_author:
                raise ValueError("author unavailable")
            return await super().tool_turn(messages, tools)
    model = InterruptedAuthor()
    store = EnterpriseStore(tmp_path / "state.sqlite")
    config = settings(tmp_path)
    await histories(store, model)
    with pytest.raises(ValueError, match="author unavailable"):
        await run_cycle(store, model, Model(), config)
    failed = store.records("evolution_cycle")[0]
    prefix = copy.deepcopy(failed["authoring"]["messages"])
    model.fail_author = False
    cycle = await run_cycle(store, model, Model(), config, retry_id=failed["id"])
    assert cycle["status"] == "canary"
    assert cycle["authoring"]["messages"][:len(prefix)] == prefix
    assert cycle["authoring"]["rounds"] == 2
