"""Run real-model development, candidate validation and one frozen acceptance pass.

python -m scripts.evaluate_support --output evaluation/support-runs/first --concurrency 4
Synthetic cases and simulated diagnostics are always labelled as such.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.config import Settings
from app.llm.deepseek import DeepSeekClient
from app.support.agent import Incident, SupportAgent
from app.support.benchmark import DOCS, VERSION, dataset, fingerprint, public_incident, score
from app.support.evolution import CandidateFormatError, SkillRegistry, now, propose


def write_json(path: Path, data) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def summary(rows: list[dict]) -> dict:
    return {"cases": len(rows), "correct": sum(r["grade"]["passed"] for r in rows),
            "accuracy": round(sum(r["grade"]["passed"] for r in rows) / len(rows), 4) if rows else None,
            "tool_calls": sum(r["run"]["tool_calls"] for r in rows),
            "prompt_tokens": sum(r["run"]["usage"]["prompt_tokens"] for r in rows),
            "completion_tokens": sum(r["run"]["usage"]["completion_tokens"] for r in rows)}


async def evaluate(args) -> dict:
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    cases = dataset()
    if args.per_family:
        # Smoke runs are labelled and cannot satisfy the release gate accidentally.
        counts = {}
        selected = []
        for case in cases:
            key = (case["split"], case["family"])
            counts[key] = counts.get(key, 0) + 1
            if counts[key] <= args.per_family:
                selected.append(case)
        cases = selected
    settings = Settings(deepseek_model=args.model)
    llm = DeepSeekClient(settings)
    llm.extra_body = {"thinking": {"type": "disabled"}}
    if not llm.api_key:
        raise ValueError("真实模型实验需要 DEEPSEEK_API_KEY；未运行任何模拟模型替代")
    agent = SupportAgent(llm)
    registry = SkillRegistry(args.registry or output / "skills.sqlite")
    digest = fingerprint(cases)
    manifest = {"version": VERSION, "dataset_hash": digest, "model": args.model,
                "synthetic": True, "execution": "read_only_snapshot_simulation", "thinking": "disabled",
                "smoke": bool(args.per_family), "agent_hash": fingerprint([{
                    "agent": (Path(__file__).parents[1] / "app/support/agent.py").read_text(encoding="utf-8"),
                    "benchmark": (Path(__file__).parents[1] / "app/support/benchmark.py").read_text(encoding="utf-8"),
                    "client": (Path(__file__).parents[1] / "app/llm/client.py").read_text(encoding="utf-8"),
                    "evolution": (Path(__file__).parents[1] / "app/support/evolution.py").read_text(encoding="utf-8"),
                }]), "splits": {s: sum(c["split"] == s for c in cases) for s in ("development", "validation", "test")}}
    manifest_path = output / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
        raise ValueError("输出目录包含不同模型、代码或数据的实验；请指定新目录")
    write_json(manifest_path, manifest)
    source_dir = output / "source"
    source_dir.mkdir(exist_ok=True)
    for relative in ("app/support/agent.py", "app/support/benchmark.py", "app/support/evolution.py",
                     "app/llm/client.py", "scripts/evaluate_support.py"):
        source = Path(__file__).parents[1] / relative
        (source_dir / relative.replace("/", "__")).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    write_json(output / "cases.json", cases)
    write_json(output / "documents.json", DOCS)
    cache = {}
    traces_path = output / "traces.jsonl"
    if traces_path.exists():
        for line in traces_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            cache[row["execution_key"]] = row
    semaphore = asyncio.Semaphore(args.concurrency)

    async def run_case(case: dict, skills: list[dict]) -> dict:
        policy_hash = fingerprint([{k: s[k] for k in ("id", "digest")} for s in skills])
        key = f"{case['id']}:{policy_hash}"
        if key in cache:
            return cache[key]
        async with semaphore:
            run = await agent.diagnose(Incident.model_validate(public_incident(case)), skills)
            row = {"execution_key": key, "case_id": case["id"], "split": case["split"], "family": case["family"],
                   "domain": case["incident"]["domain"], "incident": public_incident(case), "gold": case["gold"],
                   "run": run, "grade": score(case, run), "policy_hash": policy_hash, "created_at": now()}
            # Event-loop-local synchronous append prevents interleaved JSONL records.
            with traces_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            cache[key] = row
            print(json.dumps({"split": case["split"], "case": case["id"], "passed": row["grade"]["passed"],
                              "calls": run["tool_calls"], "cached_total": len(cache)}), flush=True)
            return row

    async def batch(selected, skills):
        return await asyncio.gather(*(run_case(case, skills) for case in selected))

    dev_cases = [c for c in cases if c["split"] == "development"]
    validation = [c for c in cases if c["split"] == "validation"]
    test = [c for c in cases if c["split"] == "test"]
    baseline_dev = await batch(dev_cases, [])
    current_dev = baseline_dev
    chosen: list[dict] = []
    iterations = []
    for iteration in range(args.iterations):
        changed = False
        for domain in ("docker", "ci"):
            try:
                draft = await propose(llm, current_dev, domain)
            except CandidateFormatError as exc:
                rejection = {"iteration": iteration + 1, "domain": domain, "decision": "rejected",
                             "reason": "invalid_candidate_schema", "errors": exc.errors, "raw_candidate": exc.raw}
                iterations.append(rejection)
                write_json(output / f"rejected-schema-{iteration + 1}-{domain}.json", rejection)
                continue
            if draft is None:
                iterations.append({"iteration": iteration + 1, "domain": domain,
                                   "decision": "no_candidate", "reason": "no_development_failures_or_excess_calls"})
                continue
            identical = next((s for s in registry.list() if s["domain"] == domain and s["instructions"] == draft["instructions"]), None)
            if identical:
                iterations.append({"iteration": iteration + 1, "domain": domain, "decision": "rejected",
                                   "reason": "duplicate_candidate", "existing_skill_id": identical["id"]})
                continue
            candidate = registry.create(draft)
            candidate_bundle = [s for s in chosen if s["domain"] != domain] + [candidate]
            selected = [c for c in validation if c["incident"]["domain"] == domain]
            before = await batch(selected, chosen)
            after = await batch(selected, candidate_bundle)
            paired = [{"case_id": a["case_id"], "split": "validation", "baseline": b["grade"],
                       "candidate": a["grade"], "baseline_calls": b["run"]["tool_calls"],
                       "candidate_calls": a["run"]["tool_calls"], "loaded_skills": a["run"]["loaded_skills"]}
                      for b, a in zip(before, after)]
            candidate = registry.validate(candidate["id"], paired, digest)
            iterations.append({"iteration": iteration + 1, "domain": domain, "skill_id": candidate["id"],
                               "decision": candidate["status"], "evaluation": candidate["evaluation"]})
            if candidate["status"] == "validated":
                chosen = candidate_bundle
                changed = True
        write_json(output / "progress.json", {"stage": "development_validation", "iterations": iterations})
        if changed:
            current_dev = await batch(dev_cases, chosen)
        elif iteration + 1 >= args.iterations or not any(not r["grade"]["passed"] for r in current_dev):
            break
    # Policy is frozen before any test result is collected; test outcomes are never sent to propose().
    write_json(output / "frozen-policy.json", {"at": now(), "skills": chosen, "dataset_hash": digest})
    baseline_test = await batch(test, [])
    final_test = await batch(test, chosen)
    report = {**manifest, "completed_at": now(), "iterations": iterations,
              "baseline_development": summary(baseline_dev), "final_development": summary(current_dev),
              "baseline_test": summary(baseline_test), "final_test": summary(final_test),
              "selected_skill_ids": [s["id"] for s in chosen], "unique_model_runs": len(cache),
              "diagnostic_run_tokens": {key: sum(r["run"]["usage"][key] for r in cache.values())
                                 for key in ("prompt_tokens", "completion_tokens")},
              "test_fixes": [a["case_id"] for b, a in zip(baseline_test, final_test) if not b["grade"]["passed"] and a["grade"]["passed"]],
              "test_regressions": [a["case_id"] for b, a in zip(baseline_test, final_test) if b["grade"]["passed"] and not a["grade"]["passed"]],
              "limitations": ["合成快照诊断，不代表生产事故解决率或真实工具修复成功率",
                              "同类故障的配置组合隔离，不是未知故障机制泛化",
                              "验证集可用于多轮候选筛选，冻结测试集只在最终策略确定后使用",
                              "无候选或无收益也保留结果；没有自动补造提升",
                              "冻结验收不等于完成线上灰度；本实验不发布正式策略",
                              "Token 统计只覆盖诊断调用，不含候选生成；跨运行的模型输出仍可能存在随机波动",
                              "结构化诊断/动作/对象和证据访问评分，不保证解释文本的每句话正确"]}
    write_json(output / "report.json", report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="evaluation/support-runs/first")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--registry", default="")
    parser.add_argument("--concurrency", type=int, default=4, choices=range(1, 9))
    parser.add_argument("--iterations", type=int, default=2, choices=range(1, 4))
    parser.add_argument("--per-family", type=int, default=0, choices=range(7))
    asyncio.run(evaluate(parser.parse_args()))
