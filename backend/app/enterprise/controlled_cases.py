"""Versioned fault-injection tasks. Histories are produced by running the agent."""
import copy
import random

from app.enterprise.fixtures import dataset, digest, score


VERSION = "controlled-v1"


def controlled_dataset() -> list[dict]:
    rows = []
    splits = {"development": "learning", "validation": "validation", "test": "test"}
    for source in dataset():
        row = copy.deepcopy(source)
        row["split"] = splits[row["split"]]
        env = row["task"]["input"]["environment"]
        row["provenance"] = "synthetic_executable_environment"
        row["defects"] = []
        variant = int(env["employee"]["name"].split("-")[-1]) - 1
        env["employee"]["department"] = ["平台研发", "客户成功", "基础设施", "产品设计"][variant]
        env["policy"]["version"] = {"learning": 2, "validation": 4, "test": 7}[row["split"]]
        if row["task"]["input"]["domain"] == "expense":
            pages = {"learning": [2, 4, 9, 11], "validation": [3, 5, 8, 10], "test": [1, 6, 10, 12]}[row["split"]][variant]
            size = variant % 3 + 1
            required = list(env["materials"])
            attachments = [f"supporting_attachment_{i}" for i in range(max(0, pages*size-len(required)))]
            random.Random(digest([row["split"], row["family"], variant])).shuffle(attachments)
            env["materials"] = attachments + required
            env["material_page_size"] = size
            row["defects"].append("partial_material_view")
        if env.get("tool_failure"):
            row["defects"].append("write_outage")
        row["id"] = digest([VERSION, row["split"], row["family"], variant])[:20]
        row["task"]["id"] = row["id"]
        rows.append(row)

    # Additional policy changes and read outages, not just renamed employees.
    additions = []
    for split in splits.values():
        for domain in ("onboarding", "access", "expense"):
            base = next(r for r in rows if r["split"] == split and r["family"] == f"{domain}-0")
            for variant in range(8):
                row = copy.deepcopy(base)
                env = row["task"]["input"]["environment"]
                row["family"] = f"{domain}-{'read_outage' if domain == 'expense' and variant < 4 else 'policy_revision'}"
                env["employee"]["name"] += f"-业务变更{variant}"
                if domain == "expense" and variant < 4:
                    env.update(material_api_failure=True, material_page_size=1, tool_failure=False)
                    env["materials"] = ["invoice", "itinerary", "business_purpose"]
                    row["gold"] = {"requests": [], "missing_information": [], "status": "blocked"}
                    row["defects"] = ["material_read_outage"]
                elif domain == "expense":
                    extra = {"learning": "cost_center", "validation": "project_code", "test": "budget_owner"}[split]
                    env["policy"]["content"] += f" 本版本所有报销额外需要材料 {extra}，缺失时同样不提交。"
                    if variant % 2:
                        env["materials"].append(extra)
                    else:
                        row["gold"] = {"requests": [], "missing_information": [extra], "status": "needs_information"}
                elif domain == "onboarding":
                    kind = {"learning": "orientation", "validation": "security_training", "test": "asset_register"}[split]
                    env["policy"]["request_types"].append({"kind": kind, "department": "人事", "approval_required": True})
                    env["policy"]["content"] += f" 本版本所有入职还需创建 {kind} 申请。"
                    row["gold"]["requests"].append(kind)
                else:
                    env["policy"]["content"] = "本版本任何资源都必须提交 production_review，由安全部门审核。不能直接开通，不能叠加 standard_access。"
                    row["gold"]["requests"] = ["production_review"]
                row["task"]["input"]["question"] = f"请按当前版本政策处理{env['employee']['name']}的{domain}任务，具体资料见档案。"
                row["id"] = digest([VERSION, split, domain, "additional", variant])[:20]
                row["task"]["id"] = row["id"]
                additions.append(row)
    return rows + additions


def score_controlled(case: dict, run: dict) -> dict:
    observed = copy.deepcopy(run)
    observed["tool_trace"].extend(t for t in run.get("recipe_trace", []) if "result" in t)
    return score(case, observed)


def learning_cases(rows: list[dict]) -> list[dict]:
    # Fixed selection before outcomes are known: 24 tasks per domain.
    return [r for r in rows if r["split"] == "learning" and r["family"].split("-")[-1].isdigit()]
