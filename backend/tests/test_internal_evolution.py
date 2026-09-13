"""Offline protocol/lifecycle verification, not claims of real-model improvement."""
import copy
import json
import sys

import httpx
import pytest

from app.config import Settings
from app.enterprise.assessment import judge_pair, summarize, execute
from app.enterprise.evolution import (active, advance, expire_trials, lifecycle_commit, observe_episode,
                                      observed_tasks, partition, propose, run_cycle, select_catalog)
from app.enterprise.examples import examples
from app.enterprise.identity import digest
from app.enterprise.runtime import build_runtime
from app.enterprise.service import EnterpriseService
from app.enterprise.store import EnterpriseStore
from app.support.evolution import now


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
            return {"evaluation": None if noop else evaluation, "change": {"action": self.action, "reason": "多个任务没有按当前材料正确办理", "evidence_ids": [t["id"] for t in data["discovery"][:2]],
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
async def test_program_facts_override_judge_success(tmp_path):
    model = Model(default_good=True)
    t = task("j")
    good = await execute(model, t, [])
    bad = copy.deepcopy(good)
    bad["requests"][0]["status"] = "granted"
    verdict = await judge_pair(model, t, good, bad, ["检查"], "key")
    assert verdict["candidate"]["judge_passed"] is True
    assert verdict["candidate"]["passed"] is False


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
