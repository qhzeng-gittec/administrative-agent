"""Enterprise tasks and internal evolution. No benchmark publication routes."""
import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from app.api.deps import require_admin, require_user
from app.api.schemas import ApiResponse
from app.enterprise.agent import TaskInput
from app.enterprise.evolution import lifecycle_commit
from app.enterprise.examples import examples as task_examples
from app.enterprise.service import EnterpriseService
from app.support.evolution import now

router = APIRouter(prefix="/enterprise", tags=["enterprise-evolution"])


async def global_admin(user=Depends(require_admin)):
    if user.get("dept_id"):
        raise HTTPException(403, "仅全局管理员可管理内部进化")
    return user


def service(request: Request):
    return EnterpriseService(request.app.state.container.settings)


def model_ready(svc):
    if not svc.settings.openrouter_api_key:
        raise HTTPException(503, "请配置 OPENROUTER_API_KEY")


@router.get("/examples", response_model=ApiResponse)
async def examples(user=Depends(require_user)):
    return ApiResponse(data=task_examples())


@router.get("/tasks", response_model=ApiResponse)
async def tasks(request: Request, user=Depends(require_user)):
    return ApiResponse(data=[t for t in service(request).store.records("task")
        if t["user_id"] == user["id"] and t.get("origin") == "workbench_task"])


@router.post("/tasks", response_model=ApiResponse)
async def create_task(body: TaskInput, request: Request, user=Depends(require_user)):
    svc = service(request)
    model_ready(svc)
    env = body.environment
    if len(json.dumps(env, ensure_ascii=False)) > 24000:
        raise HTTPException(422, "任务环境超过 24000 字符")
    if not isinstance(env.get("employee"), dict) or not isinstance(env.get("policy"), dict):
        raise HTTPException(422, "任务环境缺少 employee 或 policy")
    policy = env["policy"]
    if policy.get("status") != "active" or not isinstance(policy.get("content"), str) or not isinstance(policy.get("request_types"), list):
        raise HTTPException(422, "模拟政策必须有效且包含正文与申请类型")
    if any(not isinstance(r, dict) or not isinstance(r.get("kind"), str) or not isinstance(r.get("department"), str)
           or not isinstance(r.get("approval_required"), bool) for r in policy["request_types"]):
        raise HTTPException(422, "申请类型结构无效")
    if "material_page_size" in env and (type(env["material_page_size"]) is not int or env["material_page_size"] < 1):
        raise HTTPException(422, "分页大小必须为正整数")
    for name in ("materials", "requests"):
        if name in env and not isinstance(env[name], list):
            raise HTTPException(422, f"{name}必须是列表")
    if any(not isinstance(r, dict) or not {"kind", "status"} <= r.keys() for r in env.get("requests", [])):
        raise HTTPException(422, "已有申请结构无效")
    task_id = uuid.uuid4().hex
    message = svc.add_message(user["id"], body.question, task_id)
    row = svc.store.put("task", {"id": task_id, "user_id": user["id"], "input": body.model_dump(),
        "created_at": now(), "simulation": True, "origin": "workbench_task"})
    job = svc.store.enqueue("execute", {"task_id": task_id, "text": body.question, "message_id": message["id"]})
    return ApiResponse(data={"task": row, "job": job})


class MessageInput(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    task_id: str | None = None


@router.post("/messages", response_model=ApiResponse)
async def message(body: MessageInput, request: Request, user=Depends(require_user)):
    svc = service(request)
    model_ready(svc)
    if body.task_id:
        task = svc.store.get("task", body.task_id)
        if task is None or task["user_id"] != user["id"]:
            raise HTTPException(404, "任务不存在")
        if any(j["type"] == "execute" and j["payload"]["task_id"] == body.task_id and j["status"] in {"queued", "running"} for j in svc.store.records("job")):
            raise HTTPException(409, "该任务仍在执行，请等待结果")
    row = svc.add_message(user["id"], body.text, body.task_id)
    if body.task_id:
        svc.store.enqueue("execute", {"task_id": body.task_id, "text": body.text, "message_id": row["id"]})
    return ApiResponse(data=row)


@router.get("/messages", response_model=ApiResponse)
async def messages(request: Request, user=Depends(require_user)):
    return ApiResponse(data=[{k: v for k, v in m.items() if k not in {"context", "snapshots", "reported_attempt"}}
        for m in service(request).store.records("message") if m["user_id"] == user["id"] and m.get("origin") == "workbench_message"])


@router.get("/jobs", response_model=ApiResponse)
async def jobs(request: Request, user=Depends(require_user)):
    svc = service(request)
    owned = {t["id"] for t in svc.store.records("task") if t["user_id"] == user["id"]}
    messages = {m["id"] for m in svc.store.records("message") if m["user_id"] == user["id"]}
    return ApiResponse(data=[j for j in svc.store.records("job") if j["payload"].get("task_id") in owned or j["payload"].get("message_id") in messages])


@router.get("/evolution", response_model=ApiResponse)
async def evolution(request: Request, user=Depends(global_admin)):
    svc = service(request)
    cycles = [{k: v for k, v in c.items() if k not in {"partitions", "discovery_feedback", "authoring"}} | {
        "partition_counts": {name: len(rows) for name, rows in c["partitions"].items()}}
        for c in svc.store.records("evolution_cycle", 30)]
    return ApiResponse(data={"source": "internal_evolution", "cycles": cycles,
        "capabilities": svc.store.records("capability"), "last_run": svc.store.records("mining_run", 1),
        "jobs": [j for j in svc.store.records("job", 100) if j["type"] in {"mine", "observe"}],
        "config": {"window": 500, "min_cluster": svc.settings.skill_min_cluster, "min_signals": svc.settings.mining_min_signals,
            "interval_seconds": svc.settings.mining_interval_seconds, "judge_model": svc.settings.evolution_judge_model}})


@router.get("/evolution/{cycle_id}", response_model=ApiResponse)
async def cycle_detail(cycle_id: str, request: Request, user=Depends(global_admin)):
    store = service(request).store
    cycle = store.get("evolution_cycle", cycle_id)
    if cycle is None:
        raise HTTPException(404, "进化周期不存在")
    return ApiResponse(data={"cycle": cycle, "pairs": [r for r in store.records("evolution_pair", 10000) if r["cycle_id"] == cycle_id]})


@router.post("/evolution", response_model=ApiResponse)
async def start_evolution(request: Request, user=Depends(global_admin)):
    svc = service(request)
    model_ready(svc)
    if any(j["type"] == "mine" and j["status"] in {"queued", "running"} for j in svc.store.records("job")):
        raise HTTPException(409, "已有进化作业运行中")
    return ApiResponse(data=svc.store.enqueue("mine", {"source": "admin", "requested_by": user["id"]}))


@router.post("/evolution/{cycle_id}/rollback", response_model=ApiResponse)
async def rollback(cycle_id: str, request: Request, user=Depends(global_admin)):
    try:
        return ApiResponse(data=lifecycle_commit(service(request).store, cycle_id, "rolled_back"))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/evolution/{cycle_id}/retry", response_model=ApiResponse)
async def retry_cycle(cycle_id: str, request: Request, user=Depends(global_admin)):
    svc = service(request)
    model_ready(svc)
    cycle = svc.store.get("evolution_cycle", cycle_id)
    if not cycle or cycle["status"] != "failed":
        raise HTTPException(409, "只能重试失败周期，保留已冻结的案例和候选")
    if any(j["type"] == "mine" and j["status"] in {"queued", "running"} for j in svc.store.records("job")):
        raise HTTPException(409, "已有进化作业运行中")
    return ApiResponse(data=svc.store.enqueue("mine", {"cycle_id": cycle_id, "requested_by": user["id"]}))


@router.post("/jobs/{job_id}/retry", response_model=ApiResponse)
async def retry(job_id: str, request: Request, user=Depends(global_admin)):
    svc = service(request)
    job = svc.store.get("job", job_id)
    if not job or job["status"] != "failed":
        raise HTTPException(409, "只能重试失败作业")
    model_ready(svc)
    payload = {**job["payload"], "retry_of": job_id}
    if job["type"] == "execute":
        payload["execution_id"] = job["payload"].get("execution_id", job_id)
    return ApiResponse(data=svc.store.enqueue(job["type"], payload))
