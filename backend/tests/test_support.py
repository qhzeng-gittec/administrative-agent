"""Protocol, dataset isolation, release gates and actual state transitions."""
import json
import sqlite3
from copy import deepcopy

import pytest

from app.support.agent import Incident, SupportAgent
from app.support.benchmark import dataset, fingerprint, public_incident, score
from app.support.evolution import CandidateFormatError, SkillRegistry, paired_gate, propose


def call(name, args, id="call-1"):
    return {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class ScriptedModel:
    """Protocol fixture only; never used by live evaluation."""
    def __init__(self, turns):
        self.turns = iter(turns)
        self.messages = []

    async def tool_turn(self, messages, tools):
        self.messages.append(deepcopy(messages))
        result = next(self.turns)
        if isinstance(result, dict):
            return result
        return {"message": {"role": "assistant", "content": None, "tool_calls": result},
                "finish_reason": "tool_calls", "usage": {}}


def answer(case):
    return {k: case["gold"][k] for k in ("cause", "action", "target")} | {
        "evidence": ["config", "runtime", "logs"], "explanation": "检查现有配置与日志得到诊断。"}


@pytest.mark.asyncio
async def test_native_protocol_and_no_gold():
    case = dataset()[0]
    model = ScriptedModel([
        [call("read_observations", {"sources": ["config", "runtime", "logs"]}, "read")],
        [call("submit_diagnosis", answer(case), "finish")],
    ])
    run = await SupportAgent(model).diagnose(Incident(**public_incident(case)))
    assert score(case, run)["passed"]
    assert model.messages[1][-1]["tool_call_id"] == "read"
    assert model.messages[1][-2]["tool_calls"][0]["id"] == "read"
    prompt = json.dumps(model.messages[0])
    assert case["id"] not in prompt
    assert "gold" not in prompt and "template_group" not in prompt


@pytest.mark.asyncio
async def test_denied_tools_and_evidence_bypass_return_matching_errors():
    case = dataset()[0]
    model = ScriptedModel([
        [call("shell", {"cmd": "rm -rf /"}, "denied"), call("submit_diagnosis", answer(case), "early")],
        [call("read_observations", {"sources": ["config", "runtime", "logs"]}, "read")],
        [call("submit_diagnosis", answer(case), "finish")],
    ])
    run = await SupportAgent(model).diagnose(Incident(**public_incident(case)))
    assert score(case, run)["passed"]
    assert [r["id"] for r in run["tool_trace"]] == ["denied", "early", "read", "finish"]
    assert "error" in run["tool_trace"][0]["result"]
    assert "error" in run["tool_trace"][1]["result"]


@pytest.mark.asyncio
async def test_skill_changes_observed_instructions_not_tool_permissions():
    case = dataset()[0]
    model = ScriptedModel([
        [call("load_skill", {"skill_id": "s"}, "load")],
        [call("read_observations", {"sources": ["config", "runtime", "logs"]}, "read")],
        [call("submit_diagnosis", answer(case), "finish")],
    ])
    skill = {"id": "s", "name": "诊断", "domain": "docker", "description": "检查地址", "instructions": "先核实运行位置"}
    run = await SupportAgent(model).diagnose(Incident(**public_incident(case)), [skill])
    assert run["loaded_skills"] == ["s"]
    assert "先核实运行位置" in model.messages[1][-1]["content"]


@pytest.mark.asyncio
async def test_duplicate_ids_fail_fast():
    model = ScriptedModel([[call("search_docs", {"query": "x"}), call("search_docs", {"query": "y"})]])
    with pytest.raises(ValueError, match="重复"):
        await SupportAgent(model).diagnose(Incident(**public_incident(dataset()[0])))


@pytest.mark.asyncio
async def test_budget_exhaustion_is_not_success():
    model = ScriptedModel([[call("search_docs", {"query": "x"})]])
    run = await SupportAgent(model, max_rounds=1).diagnose(Incident(**public_incident(dataset()[0])))
    assert run["status"] == "budget_exhausted"
    assert not score(dataset()[0], run)["passed"]


@pytest.mark.asyncio
async def test_truncated_model_response_is_not_success():
    model = ScriptedModel([{"message": {"role": "assistant", "content": "..."}, "finish_reason": "length", "usage": {}}])
    run = await SupportAgent(model).diagnose(Incident(**public_incident(dataset()[0])))
    assert run["status"] == "incomplete_model_response"


def test_dataset_groups_and_payloads_are_disjoint():
    cases = dataset()
    assert len(cases) == 252 and len({c["id"] for c in cases}) == 252
    groups = {s: {c["template_group"] for c in cases if c["split"] == s} for s in ("development", "validation", "test")}
    assert not groups["development"] & groups["validation"]
    assert not groups["test"] & (groups["development"] | groups["validation"])
    assert len({fingerprint([c["incident"]]) for c in cases}) == 252
    for case in cases:
        public = public_incident(case)
        assert set(public) == {"question", "domain", "observations"}
        assert "gold" not in public


def test_fabricated_citations_fail_grader():
    case = dataset()[0]
    run = {"status": "completed", "diagnosis": answer(case), "observed_sources": ["config"]}
    assert not score(case, run)["passed"]


def pairs(fix=True):
    return [{"case_id": f"validation-{i}", "split": "validation", "baseline": {"passed": i != 0 or not fix},
             "candidate": {"passed": True}, "baseline_calls": 3, "candidate_calls": 3,
             "loaded_skills": ["candidate"]} for i in range(12)]


@pytest.mark.parametrize("change,reason", [
    ("noop", "no_measurable_benefit"), ("regression", "no_measurable_benefit"),
    ("unloaded", "skill_never_executed"), ("few", "insufficient_samples"),
])
def test_gates_reject_bad_candidates(change, reason):
    rows = pairs(fix=change != "noop")
    if change == "regression": rows[1]["candidate"]["passed"] = False
    if change == "unloaded":
        for row in rows: row["loaded_skills"] = []
    if change == "few": rows = rows[:3]
    assert reason in paired_gate(rows)["reasons"]


def draft(name="连接诊断"):
    return {"name": name, "domain": "docker", "description": "容器连接排查",
            "instructions": f"{name}：先核实运行位置，再看当前配置和日志。", "source_cases": ["development-1"]}


def test_net_improvement_with_regression_can_enter_canary(tmp_path):
    rows = pairs()
    rows[1]["baseline"]["passed"] = False
    rows[2]["candidate"]["passed"] = False
    registry = SkillRegistry(tmp_path / "skills.sqlite")
    skill = registry.create(draft())
    validated = registry.validate(skill["id"], rows, "net-benefit-fixture")
    assert validated["evaluation"]["regressions"] == ["validation-2"]
    assert len(validated["evaluation"]["fixes"]) == 2
    assert registry.start_canary(skill["id"], simulation=True)["status"] == "canary"


def test_efficiency_cannot_compensate_for_accuracy_loss():
    rows = pairs(fix=False)
    rows[0]["candidate"]["passed"] = False
    for row in rows:
        row["candidate_calls"] = 1
    assert not paired_gate(rows)["passed"]
    rows[0]["candidate"]["passed"] = True
    assert paired_gate(rows)["passed"]


def test_registry_no_bypass_and_persistence(tmp_path):
    path = tmp_path / "skills.sqlite"
    registry = SkillRegistry(path)
    skill = registry.create(draft())
    with pytest.raises(ValueError): registry.start_canary(skill["id"])
    with pytest.raises(ValueError): registry.validate(skill["id"], [dict(r, split="test") for r in pairs()], "hash")
    overlap = pairs(); overlap[0]["case_id"] = "development-1"
    with pytest.raises(ValueError, match="重叠"): registry.validate(skill["id"], overlap, "hash")
    skill = registry.validate(skill["id"], pairs(), "hash")
    assert skill["status"] == "validated"
    with pytest.raises(ValueError): registry.validate(skill["id"], pairs(), "hash")
    assert SkillRegistry(path).list()[0]["evaluation"]["dataset_hash"] == "hash"
    assert registry.start_canary(skill["id"])["status"] == "canary"


def test_noop_skill_rejected_and_cannot_publish(tmp_path):
    reg = SkillRegistry(tmp_path / "skills.sqlite")
    skill = reg.create(draft())
    assert reg.validate(skill["id"], pairs(fix=False), "hash")["status"] == "rejected"
    with pytest.raises(ValueError): reg.start_canary(skill["id"])


@pytest.mark.parametrize("simulation", [False, True])
def test_canary_terminal_decision_and_simulation_isolation(tmp_path, simulation):
    reg = SkillRegistry(tmp_path / "skills.sqlite")
    skill = reg.create(draft())
    reg.validate(skill["id"], pairs(), "hash")
    reg.start_canary(skill["id"], simulation=simulation)
    for i in range(1000):
        subject = f"independent-user-{i}"
        treatment = reg.cohort(skill["id"], subject) == "treatment"
        skill = reg.observe(skill["id"], subject, treatment or i % 3 == 0, simulation=simulation)
        if i < 999: assert skill["status"] == "canary"
    assert min(skill["samples"].values()) >= 30
    assert skill["status"] == ("simulation_completed" if simulation else "active")
    assert skill["p_value"] < 0.05
    assert bool(reg.select("docker", "reader")) != simulation


def test_canary_deduplicates_users(tmp_path):
    reg = SkillRegistry(tmp_path / "skills.sqlite")
    skill = reg.create(draft()); reg.validate(skill["id"], pairs(), "hash"); reg.start_canary(skill["id"])
    reg.observe(skill["id"], "user-1", True)
    with pytest.raises(sqlite3.IntegrityError): reg.observe(skill["id"], "user-1", False)
    with pytest.raises(ValueError): reg.observe(skill["id"], "user-2", True, simulation=True)


def test_failed_gray_and_predecessor_restoration(tmp_path):
    reg = SkillRegistry(tmp_path / "skills.sqlite")
    old = reg.create(draft()); reg.validate(old["id"], pairs(), "hash"); reg.start_canary(old["id"])
    for i in range(1000):
        subject = str(i)
        reg.observe(old["id"], subject, reg.cohort(old["id"], subject) == "treatment" or i % 3 == 0)
    new = reg.create(draft("新诊断"), replaces=old["id"])
    assert new["version"] == 2
    reg.validate(new["id"], pairs(), "hash"); reg.start_canary(new["id"])
    for i in range(1000):
        subject = str(i)
        result = reg.observe(new["id"], subject, reg.cohort(new["id"], subject) == "control")
    assert result["status"] == "rolled_back"
    assert reg.select("docker", "any")[0]["id"] == old["id"]


@pytest.mark.asyncio
async def test_proposer_never_reads_validation_or_test():
    class Model:
        async def complete_json(self, messages, **kwargs):
            text = json.dumps(messages)
            assert "DO_NOT_LEAK" not in text
            return {k: v for k, v in draft().items() if k != "source_cases"}
    case = dataset()[0]
    row = {"case_id": case["id"], "split": "development", "domain": "docker", "grade": {"passed": False, "checks": {}},
           "incident": case["incident"], "gold": case["gold"], "run": {"diagnosis": None, "tool_calls": 2, "tool_trace": []}}
    hidden = {**row, "split": "test", "incident": {"question": "DO_NOT_LEAK"}}
    result = await propose(Model(), [row, hidden], "docker")
    assert result["source_cases"] == [case["id"]]


def test_expired_canary_closes_without_more_traffic(tmp_path):
    reg = SkillRegistry(tmp_path / "skills.sqlite")
    skill = reg.create(draft()); reg.validate(skill["id"], pairs(), "hash"); reg.start_canary(skill["id"])
    with reg.connection() as db:
        row = reg._get(db, skill["id"])
        row["canary_started_at"] = "2020-01-01T00:00:00+00:00"
        reg._save(db, row)
    assert reg.select("docker", "user") == []
    assert reg.list()[0]["status"] == "inconclusive"


@pytest.mark.asyncio
async def test_proposer_prioritizes_failures_over_unrelated_costs():
    class Model:
        async def complete_json(self, messages, **kwargs):
            text = json.dumps(messages)
            assert "COST_ONLY" not in text
            return {k: v for k, v in draft().items() if k != "source_cases"}
    case = dataset()[0]
    row = {"case_id": "failure", "split": "development", "domain": "docker", "grade": {"passed": False, "checks": {}},
           "incident": case["incident"], "gold": case["gold"], "run": {"diagnosis": None, "tool_calls": 2, "tool_trace": []}}
    costly = {**row, "case_id": "cost", "grade": {"passed": True}, "run": {"tool_calls": 5}, "incident": {"question": "COST_ONLY"}}
    result = await propose(Model(), [costly, row], "docker")
    assert result["source_cases"] == ["failure"]


@pytest.mark.asyncio
async def test_invalid_candidate_is_auditable_rejection():
    class Model:
        async def complete_json(self, messages, **kwargs):
            return {k: v for k, v in draft().items() if k != "source_cases"} | {"_note": "unexpected"}
    case = dataset()[0]
    row = {"case_id": "failure", "split": "development", "domain": "docker", "grade": {"passed": False, "checks": {}},
           "incident": case["incident"], "gold": case["gold"], "run": {"diagnosis": None, "tool_calls": 2, "tool_trace": []}}
    with pytest.raises(CandidateFormatError) as failure:
        await propose(Model(), [row], "docker")
    assert failure.value.raw["_note"] == "unexpected"
    assert failure.value.errors[0]["type"] == "extra_forbidden"


def test_support_feedback_survives_registry_restart(tmp_path):
    path = tmp_path / "skills.sqlite"
    registry = SkillRegistry(path)
    registry.save_run({"_id": "incident", "user_id": "employee", "feedback": {"resolved": False}})
    assert SkillRegistry(path).get_run("incident")["feedback"] == {"resolved": False}
