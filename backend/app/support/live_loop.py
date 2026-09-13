"""Turn reviewed support incidents into candidates; never learn from raw thumbs-down."""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.llm.deepseek import DeepSeekClient
from app.support.agent import Incident, SupportAgent
from app.support.benchmark import dataset, fingerprint, score
from app.support.evolution import CandidateFormatError, SkillRegistry, propose


async def improve_reviewed(container, domain: str, job_id: str) -> dict:
    registry = SkillRegistry(Path(container.settings.support_data_dir) / "skills.sqlite")
    records = registry.runs()
    development = []
    for row in records:
        resolution = row.get("reviewed_resolution")
        if not resolution or row["incident"]["domain"] != domain:
            continue
        case = {"gold": {**resolution["diagnosis"], "required_sources": ["config", "runtime", "logs"]}}
        development.append({"case_id": row["_id"], "split": "development", "domain": domain,
                            "incident": row["incident"], "run": row["run"], "gold": case["gold"],
                            "grade": score(case, row["run"])})
    if not development:
        return {"decision": "no_candidate", "reason": "没有经管理员核实处理结果的记录"}
    llm = DeepSeekClient(container.settings.model_copy(update={"deepseek_model": container.settings.support_model}))
    llm.extra_body = {"thinking": {"type": "disabled"}}
    from scripts.evaluate_support import write_json
    output = Path(container.settings.support_data_dir) / "reviewed-experiments" / job_id
    output.mkdir(parents=True, exist_ok=True)
    try:
        draft = await propose(llm, development, domain)
    except CandidateFormatError as exc:
        result = {"decision": "rejected", "reason": "invalid_candidate_schema", "errors": exc.errors, "raw_candidate": exc.raw}
        write_json(output / "rejection.json", result)
        return result
    if draft is None:
        return {"decision": "no_candidate", "reason": "已审核记录没有错误或高调用成本"}
    old = [s for s in registry.list() if s["domain"] == domain and s["status"] == "active"]
    candidate = registry.create(draft, replaces=old[0]["id"] if old else None)
    cases = [c for c in dataset() if c["split"] == "validation" and c["incident"]["domain"] == domain]
    agent = SupportAgent(llm)
    sem = asyncio.Semaphore(4)

    async def replay(case):
        async with sem:
            incident = Incident(**case["incident"])
            baseline = await agent.diagnose(incident, old)
            changed = await agent.diagnose(incident, [candidate])
            return {"case_id": case["id"], "split": "validation", "baseline": score(case, baseline),
                    "candidate": score(case, changed), "baseline_calls": baseline["tool_calls"],
                    "candidate_calls": changed["tool_calls"], "loaded_skills": changed["loaded_skills"],
                    "baseline_run": baseline, "candidate_run": changed}

    await container.job_queue.update_progress(job_id, {"stage": "validation", "detail": f"正在回放 {len(cases)} 个独立验证案例"})
    pairs = await asyncio.gather(*(replay(c) for c in cases))
    candidate = registry.validate(candidate["id"], pairs, fingerprint(cases))
    write_json(output / "replay.json", pairs)
    write_json(output / "candidate.json", candidate)
    return {"decision": candidate["status"], "skill_id": candidate["id"], "evaluation": candidate["evaluation"],
            "source": "human_reviewed_incidents", "validation": "synthetic_held_out_snapshots"}
