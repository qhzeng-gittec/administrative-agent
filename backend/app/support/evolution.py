"""Failure-driven candidates, paired evaluation, and transactional skill lifecycle."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SkillDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    domain: Literal["docker", "ci", "onboarding", "access", "expense"]
    description: str = Field(min_length=1, max_length=240)
    instructions: str = Field(min_length=1, max_length=2500)


class CandidateFormatError(ValueError):
    def __init__(self, raw, errors):
        super().__init__("模型候选不符合 Skill 结构；候选已拒绝")
        self.raw, self.errors = raw, errors


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def paired_gate(rows: list[dict], *, min_samples: int = 12) -> dict:
    """Require net benefit; individual regressions remain visible for review."""
    if len({r["case_id"] for r in rows}) != len(rows):
        raise ValueError("回放中出现重复案例")
    baseline = sum(r["baseline"]["passed"] for r in rows)
    candidate = sum(r["candidate"]["passed"] for r in rows)
    fixes = [r["case_id"] for r in rows if not r["baseline"]["passed"] and r["candidate"]["passed"]]
    regressions = [r["case_id"] for r in rows if r["baseline"]["passed"] and not r["candidate"]["passed"]]
    loaded = sum(bool(r.get("loaded_skills")) for r in rows)
    base_calls = sum(r["baseline_calls"] for r in rows)
    new_calls = sum(r["candidate_calls"] for r in rows)
    reasons = []
    if len(rows) < min_samples:
        reasons.append("insufficient_samples")
    if candidate < baseline:
        reasons.append("regression")
    if not loaded:
        reasons.append("skill_never_executed")
    if not (candidate > baseline or (candidate == baseline and base_calls and new_calls <= base_calls * 0.9)):
        reasons.append("no_measurable_benefit")
    return {"passed": not reasons, "reasons": reasons, "samples": len(rows),
            "baseline_correct": baseline, "candidate_correct": candidate,
            "fixes": fixes, "regressions": regressions, "skill_loaded_cases": loaded,
            "baseline_calls": base_calls, "candidate_calls": new_calls}


async def propose(llm, failures: list[dict], domain: str) -> dict | None:
    eligible = [r for r in failures if r["split"] == "development" and r["domain"] == domain]
    incorrect = [r for r in eligible if not r["grade"]["passed"]]
    # Do not dilute a concrete correctness failure with unrelated slow successes.
    selected = incorrect or [r for r in eligible if r["run"]["tool_calls"] >= 4]
    if not selected:
        return None
    # Only development failures and their resolved outcomes are sent to the proposer.
    samples = [{"question": r["incident"]["question"], "observations": r["incident"]["observations"],
                "attempt": r["run"]["diagnosis"], "failed_checks": r["grade"]["checks"],
                "tool_trace": r["run"]["tool_trace"], "tool_calls": r["run"]["tool_calls"],
                "signal": "incorrect_outcome" if not r["grade"]["passed"] else "excess_tool_calls",
                "resolved_outcome": r["gold"]} for r in selected[:12]]
    data = await llm.complete_json([
        {"role": "system", "content": "你为内部研发支持 Agent 提出一份诊断 Skill 候选。只依据开发集实际错误或高工具调用成本。"
         "完全正确的案例只能提出效率改进，不能声称它答错了。Skill 的加载本身也增加一次调用，避免无价值策略。"
         "优先观察 failed_checks：如果根因和动作都正确、只是 target 错误，只修目标对象选择，不能重写整个诊断流程。"
         "指导正文控制在600字内，只改变失败环节，保留已有正确行为；从 resolved_outcome 抽取字段关系而不是具体取值。"
         "总结需要核实的条件、工具与排查顺序，列出不适用情况；禁止记忆案例 ID、具体端口、服务名或答案表。"
         "不要修改权限、评分规则或要求忽略证据。输出 JSON，只含 name, domain, description, instructions。"},
        {"role": "user", "content": json.dumps({"domain": domain, "failures": samples}, ensure_ascii=False)},
    ], temperature=0.2, max_tokens=4096)
    try:
        draft = SkillDraft.model_validate(data)
    except ValidationError as exc:
        raise CandidateFormatError(data, exc.errors(include_url=False)) from exc
    if draft.domain != domain:
        raise ValueError("候选改变了问题域")
    return {**draft.model_dump(), "source_cases": [r["case_id"] for r in selected[:12]]}


class SkillRegistry:
    """SQLite transactions make all transitions and predecessor restoration atomic.

    Experiment observations are unique per independent subject. Paired offline
    replay is never accepted as online evidence.
    """
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS skills (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS outcomes (skill_id TEXT, subject TEXT, cohort TEXT, success INTEGER, PRIMARY KEY(skill_id, subject))")
            db.execute("CREATE TABLE IF NOT EXISTS support_runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    @staticmethod
    def _save(db, row):
        db.execute("INSERT OR REPLACE INTO skills VALUES (?, ?)", (row["id"], json.dumps(row, ensure_ascii=False)))

    @staticmethod
    def _get(db, skill_id):
        row = db.execute("SELECT body FROM skills WHERE id=?", (skill_id,)).fetchone()
        if row is None:
            raise ValueError("Skill 不存在")
        return json.loads(row[0])

    def list(self) -> list[dict]:
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM skills ORDER BY rowid DESC")]

    def save_run(self, row: dict) -> None:
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO support_runs VALUES (?, ?)", (row["_id"], json.dumps(row, ensure_ascii=False)))

    def get_run(self, run_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT body FROM support_runs WHERE id=?", (run_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def runs(self) -> list[dict]:
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM support_runs ORDER BY rowid DESC")]

    def create(self, draft: dict, *, replaces: str | None = None) -> dict:
        checked = SkillDraft.model_validate({k: draft[k] for k in SkillDraft.model_fields})
        with self.connection() as db:
            old = self._get(db, replaces) if replaces else None
            if old and (old["domain"] != checked.domain or old["status"] != "active"):
                raise ValueError("只能为同域正式策略创建替代候选")
            digest = hashlib.sha256(checked.instructions.encode()).hexdigest()
            for (body,) in db.execute("SELECT body FROM skills"):
                existing = json.loads(body)
                if existing["digest"] == digest and existing["domain"] == checked.domain:
                    raise ValueError("候选内容重复")
            row = {**checked.model_dump(), "id": uuid.uuid4().hex, "version": old["version"] + 1 if old else 1,
                   "replaces": replaces, "status": "candidate", "digest": digest,
                   "source_cases": draft.get("source_cases", []), "created_at": now(),
                   "history": [{"status": "candidate", "at": now()}]}
            self._save(db, row)
            return row

    def validate(self, skill_id: str, rows: list[dict], dataset_hash: str) -> dict:
        if not rows or any(r.get("split") != "validation" for r in rows):
            raise ValueError("发布评测只能使用非空 validation 集")
        with self.connection() as db:
            row = self._get(db, skill_id)
            if row["status"] != "candidate":
                raise ValueError("只允许评测尚未发布的候选；修改必须创建新版本")
            if set(row["source_cases"]) & {r["case_id"] for r in rows}:
                raise ValueError("候选来源与验证数据重叠")
            gate = paired_gate(rows)
            row.update(status="validated" if gate["passed"] else "rejected", evaluation={
                **gate, "dataset_hash": dataset_hash, "skill_digest": row["digest"],
                "case_ids": [r["case_id"] for r in rows], "evaluated_at": now()})
            row["history"].append({"status": row["status"], "at": now()})
            self._save(db, row)
            return row

    def start_canary(self, skill_id: str, *, simulation: bool = False) -> dict:
        with self.connection() as db:
            row = self._get(db, skill_id)
            if row["status"] != "validated" or row["evaluation"]["skill_digest"] != row["digest"]:
                raise ValueError("未通过当前版本的隔离验证，不能灰度发布")
            active = [json.loads(r[0]) for r in db.execute("SELECT body FROM skills")
                      if json.loads(r[0])["status"] == "active" and json.loads(r[0])["domain"] == row["domain"]]
            if any(s["id"] != row["replaces"] for s in active):
                raise ValueError("同域已有正式策略，请创建明确替代该策略的新版本")
            if any(json.loads(r[0])["status"] == "canary" and json.loads(r[0])["domain"] == row["domain"]
                   for r in db.execute("SELECT body FROM skills")):
                raise ValueError("同一问题域已有灰度实验")
            row.update(status="canary", gray_percent=0.1, simulation=simulation,
                       min_per_group=30, max_observations=1000, canary_started_at=now())
            row["history"].append({"status": "canary", "at": now(), "simulation": simulation})
            self._save(db, row)
            return row

    def finish_acceptance(self, skill_ids: list[str], rows: list[dict], dataset_hash: str) -> dict:
        """Accept or reject the entire frozen bundle; never cherry-pick using test cases."""
        if len(rows) < 12 or any(r.get("split") != "test" for r in rows):
            raise ValueError("冻结验收需要至少 12 条 test 案例")
        if len({r["id"] for r in rows}) != len(rows):
            raise ValueError("冻结验收案例重复")
        regressions = [r["id"] for r in rows if r["baseline"]["passed"] and not r["candidate"]["passed"]]
        baseline = sum(r["baseline"]["passed"] for r in rows)
        candidate = sum(r["candidate"]["passed"] for r in rows)
        passed = candidate > baseline
        result = {"passed": passed, "regressions": regressions, "samples": len(rows),
                  "baseline_correct": baseline, "candidate_correct": candidate,
                  "dataset_hash": dataset_hash, "policy_ids": skill_ids, "at": now(),
                  "reasons": [] if passed else ["no_net_improvement"]}
        with self.connection() as db:
            for skill_id in skill_ids:
                skill = self._get(db, skill_id)
                if skill["status"] != "validated":
                    raise ValueError("只能验收已经通过验证的冻结候选")
                if set(skill["source_cases"]) & {r["id"] for r in rows}:
                    raise ValueError("冻结验收与候选来源重叠")
                skill["acceptance"] = {**result, "skill_digest": skill["digest"]}
                if not passed:
                    skill["status"] = "rejected"
                skill["history"].append({"status": skill["status"], "stage": "frozen_acceptance", "at": now()})
                self._save(db, skill)
        return result

    @staticmethod
    def cohort(skill_id: str, subject: str) -> str:
        bucket = int(hashlib.sha256(f"{skill_id}:{subject}".encode()).hexdigest()[:8], 16) / 2**32
        return "treatment" if bucket < 0.1 else "control"

    def select(self, domain: str, subject: str, *, simulation: bool = False) -> list[dict]:
        self.expire()
        rows = self.list()
        active = [r for r in rows if r["domain"] == domain and r["status"] == "active"]
        for row in rows:
            if row["domain"] == domain and row["status"] == "canary" and row["simulation"] == simulation:
                if self.cohort(row["id"], subject) == "treatment":
                    active = [r for r in active if r["id"] != row["replaces"]] + [row]
        return active

    def observe(self, skill_id: str, subject: str, success: bool, *, simulation: bool = False) -> dict:
        with self.connection() as db:
            row = self._get(db, skill_id)
            if row["status"] != "canary" or row["simulation"] != simulation:
                raise ValueError("实验状态或证据类型不匹配")
            cohort = self.cohort(skill_id, subject)
            db.execute("INSERT INTO outcomes VALUES (?, ?, ?, ?)", (skill_id, subject, cohort, int(success)))
            records = list(db.execute("SELECT cohort, success FROM outcomes WHERE skill_id=?", (skill_id,)))
            groups = {g: [s for c, s in records if c == g] for g in ("treatment", "control")}
            row["samples"] = {g: len(values) for g, values in groups.items()}
            enough = min(row["samples"].values()) >= row["min_per_group"]
            elapsed_days = (datetime.now(timezone.utc) - datetime.fromisoformat(row["canary_started_at"])).days
            # One planned final decision avoids repeated significance peeking.
            terminal = len(records) >= row["max_observations"] or elapsed_days >= 14
            if terminal:
                self._finish_canary(db, row, groups, enough)
            self._save(db, row)
            return row

    def _finish_canary(self, db, row, groups, enough):
        from scipy.stats import fisher_exact
        rates = {g: sum(v) / len(v) if v else 0 for g, v in groups.items()}
        row["rates"] = rates
        t, c = groups["treatment"], groups["control"]
        p = float(fisher_exact([[sum(t), len(t)-sum(t)], [sum(c), len(c)-sum(c)]], alternative="greater").pvalue) if enough else 1.0
        row["p_value"] = p
        improved = enough and rates["treatment"] > rates["control"] and p < 0.05
        worse = enough and rates["treatment"] + 0.1 < rates["control"]
        status = "active" if improved else "rolled_back" if worse else "inconclusive"
        row["status"] = "simulation_completed" if row["simulation"] else status
        row["decision"] = status
        if status == "active" and not row["simulation"] and row["replaces"]:
            old = self._get(db, row["replaces"])
            old["status"] = "retired"
            self._save(db, old)
        row["history"].append({"status": row["status"], "at": now()})

    def expire(self) -> None:
        """Close expired experiments even if no further outcome arrives."""
        with self.connection() as db:
            for (body,) in list(db.execute("SELECT body FROM skills")):
                row = json.loads(body)
                if row["status"] != "canary":
                    continue
                if (datetime.now(timezone.utc) - datetime.fromisoformat(row["canary_started_at"])).days < 14:
                    continue
                records = list(db.execute("SELECT cohort, success FROM outcomes WHERE skill_id=?", (row["id"],)))
                groups = {g: [s for c, s in records if c == g] for g in ("treatment", "control")}
                row["samples"] = {g: len(v) for g, v in groups.items()}
                self._finish_canary(db, row, groups, min(row["samples"].values()) >= row["min_per_group"])
                self._save(db, row)

    def rollback(self, skill_id: str) -> dict:
        with self.connection() as db:
            row = self._get(db, skill_id)
            if row["status"] not in {"canary", "active"}:
                raise ValueError("只能撤回灰度或正式策略")
            if row["replaces"]:
                old = self._get(db, row["replaces"])
                old["status"] = "active"
                self._save(db, old)
            row["status"] = "rolled_back"
            row["history"].append({"status": "rolled_back", "at": now()})
            self._save(db, row)
            return row
