"""Engineering support UI/API. Evaluation gold and hidden cases are never public."""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import require_admin, require_user
from app.api.schemas import ApiResponse
from app.llm.deepseek import DeepSeekClient
from app.support.agent import Diagnosis, Incident, SupportAgent
from app.support.benchmark import ACTIONS, CAUSES, DOCS, dataset, public_incident
from app.support.evolution import SkillRegistry, now

router = APIRouter(prefix="/support", tags=["engineering-support"])


def registry(request: Request) -> SkillRegistry:
    return SkillRegistry(Path(request.app.state.container.settings.support_data_dir) / "skills.sqlite")


async def global_admin(user: dict = Depends(require_admin)):
    if user.get("dept_id"):
        raise HTTPException(403, "仅全局管理员可管理跨域研发策略")
    return user


@router.get("/examples", response_model=ApiResponse)
async def examples(user: dict = Depends(require_user)):
    # Only development samples. Opaque IDs, no gold/family fields in response.
    cases = [c for c in dataset() if c["split"] == "development"]
    return ApiResponse(data=[{"id": c["id"], "title": f"{c['incident']['domain'].upper()} 案例 {i+1}",
                              "incident": public_incident(c)} for i, c in enumerate(cases) if i % 6 == 0])


@router.get("/documents", response_model=ApiResponse)
async def documents(user: dict = Depends(require_user)):
    return ApiResponse(data=DOCS)


@router.post("/diagnose", response_model=ApiResponse)
async def diagnose(incident: Incident, request: Request, user: dict = Depends(require_user)):
    container = request.app.state.container
    settings = container.settings.model_copy(update={"deepseek_model": container.settings.support_model})
    llm = DeepSeekClient(settings)
    llm.extra_body = {"thinking": {"type": "disabled"}}
    if not llm.api_key:
        raise HTTPException(503, "请在服务端配置模型 API；诊断不会用模拟回答代替")
    skills = registry(request).select(incident.domain, user["id"])
    try:
        run = await SupportAgent(llm).diagnose(incident, skills)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"模型服务调用失败：{type(exc).__name__}") from exc
    run_id = uuid.uuid4().hex
    registry(request).save_run({"_id": run_id, "user_id": user["id"],
        "incident": incident.model_dump(), "run": run, "created_at": now()})
    return ApiResponse(data={"id": run_id, **run, "cause_label": CAUSES.get((run.get("diagnosis") or {}).get("cause", ""), "未完成")})


class Feedback(BaseModel):
    resolved: bool
    correction: str = Field(default="", max_length=2000)


@router.post("/runs/{run_id}/feedback", response_model=ApiResponse)
async def feedback(run_id: str, body: Feedback, request: Request, user: dict = Depends(require_user)):
    reg = registry(request)
    run = reg.get_run(run_id)
    if not run or run["user_id"] != user["id"]:
        raise HTTPException(404, "诊断记录不存在")
    run["feedback"] = {**body.model_dump(), "at": now()}
    reg.save_run(run)
    return ApiResponse(data={"saved": True, "status": "pending_human_resolution_review"})


@router.get("/skills", response_model=ApiResponse)
async def skills(request: Request, user: dict = Depends(global_admin)):
    reg = registry(request)
    reg.expire()
    return ApiResponse(data=reg.list())


@router.get("/reviews", response_model=ApiResponse)
async def reviews(request: Request, user: dict = Depends(global_admin)):
    rows = registry(request).runs()
    rows = [r for r in rows if r.get("feedback")]
    return ApiResponse(data=sorted(rows, key=lambda r: r["created_at"], reverse=True)[:50])


@router.post("/runs/{run_id}/review", response_model=ApiResponse)
async def review(run_id: str, body: Diagnosis, request: Request, user: dict = Depends(global_admin)):
    if body.cause not in CAUSES or body.action not in ACTIONS:
        raise HTTPException(422, "诊断或处理动作不属于支持的范围")
    reg = registry(request)
    row = reg.get_run(run_id)
    if not row or not row.get("feedback"):
        raise HTTPException(404, "待审核反馈不存在")
    row["reviewed_resolution"] = {"diagnosis": body.model_dump(), "reviewed_by": user["id"], "at": now()}
    reg.save_run(row)
    return ApiResponse(data={"reviewed": True})


class ImprovementRequest(BaseModel):
    domain: Literal["docker", "ci"]


@router.post("/improve", response_model=ApiResponse)
async def improve(body: ImprovementRequest, request: Request, user: dict = Depends(global_admin)):
    store = request.app.state.container.store
    records = registry(request).runs()
    if not any(r.get("reviewed_resolution") and r["incident"]["domain"] == body.domain for r in records):
        raise HTTPException(409, "先审核该问题域的处理结果，再生成候选")
    existing = await store.find("async_jobs", {"type": "support_improvement"})
    if any(j["status"] in {"queued", "running"} for j in existing):
        raise HTTPException(409, "已有反馈改进任务运行中")
    return ApiResponse(data=await request.app.state.container.job_queue.enqueue("support_improvement", {
        "domain": body.domain, "requested_by": user["id"]}))


@router.post("/skills/{skill_id}/canary", response_model=ApiResponse)
async def canary(skill_id: str, request: Request, user: dict = Depends(global_admin)):
    try:
        reg = registry(request)
        skill = next((s for s in reg.list() if s["id"] == skill_id), None)
        if skill is None:
            raise ValueError("Skill 不存在")
        return ApiResponse(data=reg.start_canary(skill_id, simulation=skill["domain"] in {"onboarding", "access", "expense"}))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/skills/{skill_id}/rollback", response_model=ApiResponse)
async def rollback(skill_id: str, request: Request, user: dict = Depends(global_admin)):
    try:
        return ApiResponse(data=registry(request).rollback(skill_id))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class ReviewedOutcome(BaseModel):
    run_id: str
    success: bool


@router.post("/skills/{skill_id}/outcomes", response_model=ApiResponse)
async def outcome(skill_id: str, body: ReviewedOutcome, request: Request, user: dict = Depends(global_admin)):
    run = registry(request).get_run(body.run_id)
    if not run:
        raise HTTPException(404, "诊断记录不存在")
    reg = registry(request)
    skill = next((s for s in reg.list() if s["id"] == skill_id), None)
    if not skill or skill["domain"] != run["incident"]["domain"] or run["created_at"] < skill.get("canary_started_at", "~"):
        raise HTTPException(409, "记录不属于此灰度时间与问题域")
    group = reg.cohort(skill_id, run["user_id"])
    loaded = skill_id in run["run"]["loaded_skills"]
    # Intention-to-treat: treatment not loading the skill is still a valid outcome.
    if group == "control" and loaded:
        raise HTTPException(409, "对照组受到候选策略污染")
    try:
        return ApiResponse(data=reg.observe(skill_id, run["user_id"], body.success))
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(409, "实验状态错误或该用户已计入本次实验") from exc


@router.get("/experiments", response_model=ApiResponse)
async def experiments(request: Request, user: dict = Depends(global_admin)):
    root = Path(request.app.state.container.settings.support_data_dir) / "experiments"
    reports = []
    for path in sorted(root.glob("*/report.json"), reverse=True)[:20]:
        reports.append({"id": path.parent.name, **json.loads(path.read_text(encoding="utf-8"))})
    jobs = [j for j in await request.app.state.container.store.find("async_jobs")
            if j["type"] in {"support_evaluation", "support_improvement"}]
    return ApiResponse(data={"reports": reports, "jobs": jobs})


@router.post("/experiments", response_model=ApiResponse)
async def start_experiment(request: Request, user: dict = Depends(global_admin)):
    queue = request.app.state.container.job_queue
    existing = await request.app.state.container.store.find("async_jobs", {"type": "support_evaluation"})
    if any(j["status"] in {"queued", "running"} for j in existing):
        raise HTTPException(409, "已有实验正在执行")
    job = await queue.enqueue("support_evaluation", {"requested_by": user["id"]})
    return ApiResponse(data=job)
