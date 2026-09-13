"""Cluster gates, feedback provenance, durable jobs and tool-state correctness."""
import copy
import json

import pytest

from app.config import Settings
from app.enterprise.agent import EnterpriseAgent
from app.enterprise.feedback import classify, task_context
from app.enterprise.fixtures import dataset, digest, feedback_cases, historical_tasks, score
from app.enterprise.mining import cluster_tasks, mine_once, reflect
from app.enterprise.service import EnterpriseService
from app.enterprise.store import EnterpriseStore


class Model:
    model = "fake-protocol-model"

    def __init__(self, response=None, turns=None):
        self.response = response
        self.turns = iter(turns or [])
        self.calls = []
        self.embeddings = 0

    async def complete_json(self, messages, **kwargs):
        self.calls.append(messages)
        return copy.deepcopy(self.response)

    async def embed(self, texts, model):
        self.embeddings += len(texts)
        return [[1.0, 0.0] for _ in texts]

    async def tool_turn(self, messages, tools):
        self.calls.append(copy.deepcopy(messages))
        return next(self.turns)


def turn(*calls, finish="tool_calls"):
    return {"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": key, "type": "function", "function": {"name": tool, "arguments": json.dumps(args)}} for key, tool, args in calls]},
        "finish_reason": finish, "usage": {}}


def seed(store, count=24, signals=3, signal="possible_error"):
    rows = historical_tasks()[:count]
    for row in rows:
        store.put("task", row)
    feedback = []
    for i in range(signals):
        row = {"id": f"message-{i}", "text": rows[i]["example_feedback"],
               "classification": {"signal": signal, "task_id": rows[i]["id"], "evidence": ""}}
        feedback.append(row)
        store.put("message", row)
    return rows, feedback


@pytest.mark.asyncio
async def test_small_model_has_only_three_signals_and_cannot_invent_tasks():
    rows = historical_tasks()[:2]
    llm = Model({"signal": "possible_error", "task_id": "another-user-task", "evidence": "漏了"})
    with pytest.raises(ValueError, match="上下文之外"):
        await classify(llm, "漏了", task_context(rows))
    llm.response = {"signal": "preference", "task_id": rows[0]["id"], "evidence": "说短一点"}
    assert (await classify(llm, "说短一点", task_context(rows)))["signal"] == "preference"
    llm.response["evidence"] = "模型编造的原文"
    with pytest.raises(ValueError, match="不是用户原文"):
        await classify(llm, "说短一点", task_context(rows))


@pytest.mark.asyncio
async def test_none_does_not_accuse_a_task():
    llm = Model({"signal": "none", "task_id": "irrelevant", "evidence": "谢谢"})
    assert (await classify(llm, "谢谢", []))["task_id"] is None


@pytest.mark.asyncio
async def test_sparse_errors_do_not_call_embedding_or_reflection(tmp_path):
    store = EnterpriseStore(tmp_path / "state.sqlite")
    seed(store, 19, 3)
    llm = Model()
    result = await mine_once(store, llm, Settings())
    assert result["decision"] == "accumulating"
    assert llm.embeddings == 0 and not llm.calls and not store.list()


@pytest.mark.asyncio
@pytest.mark.parametrize("signal,count", [("preference", 8), ("none", 8), ("possible_error", 2)])
async def test_low_signal_or_preferences_never_trigger_review(tmp_path, signal, count):
    store = EnterpriseStore(tmp_path / "state.sqlite")
    seed(store, 24, count, signal)
    model = Model()
    result = await mine_once(store, model, Settings())
    assert result["reflection_calls"] == 0 and not model.calls
    assert result["clusters"][0]["reason"] == "insufficient_signals"


@pytest.mark.asyncio
async def test_duplicate_complaints_count_one_task(tmp_path):
    store = EnterpriseStore(tmp_path / "state.sqlite")
    tasks, events = seed(store, 24, 1)
    events = [{**events[0], "id": str(i)} for i in range(20)]
    clusters = await cluster_tasks(store, Model(), Settings(), tasks, events)
    assert clusters[0]["signal_tasks"] == 1
    assert clusters[0]["suspected_error_rate"] == 1/24
    assert not clusters[0]["eligible"]


@pytest.mark.asyncio
async def test_supported_cluster_reflects_once_and_persists_across_restart(tmp_path):
    path = tmp_path / "state.sqlite"
    store = EnterpriseStore(path)
    seed(store, 24, 3)
    model = Model({"decision": "no_change", "reason": "等待审批是正常状态", "findings": [], "candidate": None})
    first = await mine_once(store, model, Settings())
    assert first["reflection_calls"] == 1
    store = EnterpriseStore(path)
    second = await mine_once(store, model, Settings())
    assert second["reflection_calls"] == 0 and len(model.calls) == 1
    assert second["clusters"][0]["decision"] == "already_processed"
    assert model.embeddings == 24  # cached embeddings survived restart
    assert store.records("batch")[0]["reflection"]["decision"] == "no_change"


@pytest.mark.asyncio
async def test_review_cannot_invent_evidence_or_change_domain():
    rows = historical_tasks()[:24]
    group = {"task_ids": [t["id"] for t in rows], "signal_task_ids": [rows[0]["id"]], "domain": "onboarding"}
    model = Model({"decision": "maintenance", "reason": "工具可能失败", "findings": [{"task_id": rows[0]["id"], "evidence_paths": ["feedback"]}], "candidate": None})
    with pytest.raises(ValueError, match="未引用"):
        await reflect(model, group, rows, [], [])
    model.response = {"decision": "skill", "reason": "修复流程", "findings": [], "candidate": {"name": "x", "domain": "expense", "description": "x", "instructions": "x"}}
    with pytest.raises(ValueError, match="共同问题"):
        await reflect(model, group, rows, [], [])


def test_durable_job_claim_and_worker_isolation(tmp_path):
    path = tmp_path / "state.sqlite"
    first = EnterpriseStore(path)
    first.enqueue("classify", {}, job_id="a")
    first.enqueue("classify", {}, job_id="a")
    first.enqueue("execute", {}, job_id="b")
    second = EnterpriseStore(path)
    assert second.claim("execute")["id"] == "b"
    assert first.claim("execute") is None
    assert first.claim("classify")["id"] == "a"
    assert second.claim("classify") is None
    assert len(first.records("job")) == 2


def test_user_context_is_scoped_and_previous_attempt_is_captured(tmp_path):
    svc = EnterpriseService(Settings(support_data_dir=str(tmp_path)))
    first, other = historical_tasks()[:2]
    first["user_id"], other["user_id"] = "alice", "bob"
    first["origin"], other["origin"] = "workbench_task", "workbench_task"
    svc.store.put("task", first)
    svc.store.put("task", other)
    message = svc.add_message("alice", "入职没办完整", first["id"])
    first["run"]["result"]["explanation"] = "后续修复后的结果"
    svc.store.put("task", first)
    persisted = svc.store.get("message", message["id"])
    assert set(persisted["snapshots"]) == {first["id"]}
    assert persisted["snapshots"][first["id"]]["run"]["result"]["explanation"] != "后续修复后的结果"
    assert not svc.store.records("batch")


@pytest.mark.asyncio
async def test_tool_protocol_and_successful_cross_department_requests():
    case = dataset()[0]
    result = {"status": "waiting_approval", "missing_information": [], "explanation": "账号设备已申请，仓库权限等待审批。"}
    model = Model(turns=[turn(("1", "read_task", {}), ("2", "read_policy", {})),
                         turn(("3", "create_request", {"kind": "mailbox"}), ("4", "create_request", {"kind": "device"}), ("5", "create_request", {"kind": "repository"})),
                         turn(("6", "finish_task", result))])
    run = await EnterpriseAgent(model).run(case["task"])
    assert score(case, run)["passed"]
    assert {r["department"] for r in run["requests"]} == {"IT", "行政"}
    assert run["tool_trace"][0]["result"]["requests"] == []
    messages = model.calls[-1]
    assert {m["tool_call_id"] for m in messages if m["role"] == "tool"} == {"1", "2", "3", "4", "5"}


@pytest.mark.asyncio
async def test_permissions_incomplete_output_and_tool_errors_are_not_success():
    model = Model(turns=[turn(("1", "grant_access", {}), ("2", "finish_task", {"status": "completed", "missing_information": []})),
                         turn(finish="length")])
    run = await EnterpriseAgent(model).run(dataset()[0]["task"])
    assert run["status"] == "incomplete_response"
    assert run["result"] is None and not run["requests"]
    assert all("error" in call["result"] for call in run["tool_trace"])


@pytest.mark.asyncio
async def test_runtime_preserves_claim_for_business_evaluation():
    case = next(c for c in dataset() if c["family"] == "access-4")
    model = Model(turns=[turn(("1", "read_task", {}), ("2", "read_policy", {})),
                         turn(("3", "finish_task", {"status": "completed", "missing_information": [], "explanation": "全部完成"})), turn(finish="length")])
    run = await EnterpriseAgent(model).run(case["task"])
    assert run["status"] == "completed"
    assert run["result"]["status"] == "completed"
    assert run["requests"][0]["status"] == "waiting_approval"
    assert not score(case, run)["passed"]


@pytest.mark.asyncio
async def test_failed_write_evidence_is_preserved_without_forcing_business_status():
    case = next(c for c in dataset() if c["family"] == "expense-5")
    model = Model(turns=[turn(("1", "read_task", {}), ("2", "read_policy", {})),
                         turn(("3", "create_request", {"kind": "expense_review"})),
                         turn(("4", "finish_task", {"status": "completed", "missing_information": [], "explanation": "流程已执行"})),
                         turn(("5", "finish_task", {"status": "blocked", "missing_information": [], "explanation": "接口写入失败"}))])
    run = await EnterpriseAgent(model).run(case["task"])
    assert "写入失败" in run["tool_trace"][-2]["result"]["error"]
    assert run["requests"] == []
    assert run["result"]["status"] == "completed"
    assert not score(case, run)["passed"]


def test_dataset_scope_uniqueness_and_gold_separation():
    cases = dataset()
    assert len(cases) == 216
    assert len({digest(c["task"]["input"]) for c in cases}) == len(cases)
    assert all("gold" not in json.dumps(c["task"]) for c in cases)
    groups = {split: {c["template_group"] for c in cases if c["split"] == split} for split in ("development", "validation", "test")}
    assert not groups["development"] & groups["test"]
    assert len(feedback_cases()) == 120
    assert len(historical_tasks()) == 72
    assert all(t["provenance"] == "authored_history_fixture" for t in historical_tasks())


def test_simulated_canary_is_isolated_from_real_selection(tmp_path):
    store = EnterpriseStore(tmp_path / "state.sqlite")
    candidate = store.create({"name": "材料核对", "domain": "expense", "description": "核对报销材料", "instructions": "先读政策再核对材料"})
    pairs = [{"case_id": f"v{i}", "split": "validation", "baseline": {"passed": i != 0}, "candidate": {"passed": True},
              "baseline_calls": 3, "candidate_calls": 4, "loaded_skills": [candidate["id"]]} for i in range(12)]
    store.validate(candidate["id"], pairs, "test-fixture")
    store.start_canary(candidate["id"], simulation=True)
    subject = next(str(i) for i in range(100) if store.cohort(candidate["id"], str(i)) == "treatment")
    assert not store.select("expense", subject)
    assert store.select("expense", subject, simulation=True)[0]["id"] == candidate["id"]
    with pytest.raises(ValueError, match="证据类型"):
        store.observe(candidate["id"], subject, True, simulation=False)


@pytest.mark.asyncio
async def test_existing_skill_revision_requires_evaluation_and_preserves_active(tmp_path, monkeypatch):
    store = EnterpriseStore(tmp_path / "state.sqlite")
    rows, _ = seed(store, 24, 3)
    old = store.create({"name": "通用入职", "domain": "onboarding", "description": "通用入职清单", "instructions": "处理通用清单"})
    # Fixture represents an already deployed predecessor, not a test publication.
    old["status"] = "active"
    with store.connection() as db:
        store._save(db, old)
    response = {"decision": "skill", "reason": "岗位清单遗漏", "findings": [
        {"task_id": r["id"], "evidence_paths": ["run.requests", "input.environment.policy.content"]} for r in rows[:3]],
        "candidate": {"name": "按岗位补齐", "domain": "onboarding", "description": "合并岗位清单", "instructions": "读取当前岗位清单，合并缺失申请"}}
    async def evaluator(reg, llm, candidate, baseline):
        assert baseline[0]["id"] == old["id"]
        assert candidate["version"] == 2 and candidate["replaces"] == old["id"]
        pairs = [{"case_id": f"validation-{i}", "split": "validation", "baseline": {"passed": i > 0}, "candidate": {"passed": True},
                  "baseline_calls": 4, "candidate_calls": 5, "loaded_skills": [candidate["id"]]} for i in range(12)]
        return reg.validate(candidate["id"], pairs, "fixture")
    monkeypatch.setattr("app.enterprise.mining.evaluate_candidate", evaluator)
    await mine_once(store, Model(response), Settings())
    assert {s["status"] for s in store.list()} == {"active", "validated"}
    assert store.select("onboarding", "employee")[0]["id"] == old["id"]


@pytest.mark.parametrize("fixes,passed", [(0, False), (1, False), (2, True)])
def test_frozen_gate_uses_net_improvement(tmp_path, fixes, passed):
    store = EnterpriseStore(tmp_path / "state.sqlite")
    skills = []
    for domain in ("onboarding", "expense"):
        row = store.create({"name": domain, "domain": domain, "description": "候选", "instructions": f"{domain}候选"})
        pairs = [{"case_id": f"v{i}", "split": "validation", "baseline": {"passed": i > 0}, "candidate": {"passed": True}, "baseline_calls": 3,
                  "candidate_calls": 4, "loaded_skills": [row["id"]]} for i in range(12)]
        store.validate(row["id"], pairs, "validation-fixture")
        skills.append(row["id"])
    rows = [{"id": f"t{i}", "split": "test", "baseline": {"passed": True}, "candidate": {"passed": i != 0}} for i in range(12)]
    for i in range(1, fixes + 1):
        rows[i]["baseline"]["passed"] = False
    result = store.finish_acceptance(skills, rows, "test-fixture")
    assert result["passed"] is passed and result["regressions"] == ["t0"]
    assert all(s["status"] == ("validated" if passed else "rejected") for s in store.list())
    for skill_id in skills:
        if passed:
            assert store.start_canary(skill_id, simulation=True)["status"] == "canary"
        else:
            with pytest.raises(ValueError, match="未通过"):
                store.start_canary(skill_id, simulation=True)
