"""Auth, feedback provenance and externally supplied snapshot boundaries."""
import pytest
import httpx
from fastapi import FastAPI

from app.api.routes.support import router
from app.support.benchmark import dataset
from app.support.evolution import SkillRegistry, now
from pathlib import Path


@pytest.fixture
async def client(fresh_container, tmp_path):
    fresh_container.settings = fresh_container.settings.model_copy(update={"support_data_dir": str(tmp_path)})
    await fresh_container.auth.seed_users()
    application = FastAPI()
    application.state.container = fresh_container
    application.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        yield client, fresh_container


def headers(container, name):
    return {"Authorization": "Bearer " + container.auth.issue_token(name)}


async def seed_run(container):
    case = dataset()[0]
    SkillRegistry(Path(container.settings.support_data_dir) / "skills.sqlite").save_run({"_id": "run", "user_id": "employee", "incident": case["incident"],
        "run": {"diagnosis": None, "loaded_skills": []}, "created_at": now()})


@pytest.mark.asyncio
async def test_examples_do_not_leak_hidden_cases_or_gold(client):
    api, container = client
    assert (await api.get("/support/examples")).status_code == 401
    response = await api.get("/support/examples", headers=headers(container, "employee"))
    assert response.status_code == 200
    examples = response.json()["data"]
    allowed = {c["id"] for c in dataset() if c["split"] == "development"}
    assert {r["id"] for r in examples} <= allowed
    assert "gold" not in response.text and "required_sources" not in response.text


@pytest.mark.asyncio
async def test_employee_and_department_admin_cannot_publish(client):
    api, container = client
    for user in ("employee", "jwc_admin"):
        auth = headers(container, user)
        assert (await api.post("/support/skills/fake/canary", headers=auth)).status_code == 403
        assert (await api.post("/support/experiments", headers=auth)).status_code == 403
        assert (await api.get("/support/reviews", headers=auth)).status_code == 403


@pytest.mark.asyncio
async def test_feedback_owner_and_review_required(client):
    api, container = client
    await seed_run(container)
    feedback = {"resolved": False, "correction": "需要核实数据库地址"}
    assert (await api.post("/support/runs/run/feedback", headers=headers(container, "student"), json=feedback)).status_code == 404
    assert (await api.post("/support/runs/run/feedback", headers=headers(container, "employee"), json=feedback)).status_code == 200
    assert (await api.post("/support/improve", headers=headers(container, "admin"), json={"domain": "docker"})).status_code == 409
    case = dataset()[0]
    diagnosis = {k: case["gold"][k] for k in ("cause", "action", "target")} | {
        "evidence": ["config", "runtime", "logs"], "explanation": "管理员核实的处理结果"}
    assert (await api.post("/support/runs/run/review", headers=headers(container, "employee"), json=diagnosis)).status_code == 403
    assert (await api.post("/support/runs/run/review", headers=headers(container, "admin"), json=diagnosis)).status_code == 200
    response = await api.post("/support/improve", headers=headers(container, "admin"), json={"domain": "docker"})
    assert response.status_code == 200
    assert response.json()["data"]["type"] == "support_improvement"
    assert (await api.post("/support/improve", headers=headers(container, "admin"), json={"domain": "docker"})).status_code == 409


@pytest.mark.asyncio
async def test_missing_model_is_explicit_not_fake_success(client):
    api, container = client
    container.settings = container.settings.model_copy(update={"deepseek_api_key": ""})
    response = await api.post("/support/diagnose", headers=headers(container, "employee"), json=dataset()[0]["incident"])
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_review_rejects_unknown_action(client):
    api, container = client
    await seed_run(container)
    response = await api.post("/support/runs/run/review", headers=headers(container, "admin"), json={
        "cause": "authentication", "action": "delete_everything", "target": "db",
        "evidence": ["logs"], "explanation": "untrusted"})
    assert response.status_code == 422
