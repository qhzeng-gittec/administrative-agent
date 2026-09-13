import httpx
import pytest
from fastapi import FastAPI

from app.api.routes.enterprise import router
from app.enterprise.fixtures import dataset, historical_tasks
from app.enterprise.service import EnterpriseService


@pytest.fixture
async def enterprise_client(fresh_container, tmp_path):
    fresh_container.settings = fresh_container.settings.model_copy(update={"support_data_dir": str(tmp_path), "openrouter_api_key": "test-key"})
    await fresh_container.auth.seed_users()
    app = FastAPI()
    app.state.container = fresh_container
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, fresh_container


def auth(container, user):
    return {"Authorization": "Bearer " + container.auth.issue_token(user)}


@pytest.mark.asyncio
async def test_task_creation_enqueues_separate_classification_and_execution(enterprise_client):
    client, container = enterprise_client
    response = await client.post("/enterprise/tasks", headers=auth(container, "employee"), json=dataset()[0]["task"]["input"])
    assert response.status_code == 200
    svc = EnterpriseService(container.settings)
    assert {j["type"] for j in svc.store.records("job")} == {"classify", "execute"}
    assert not svc.store.records("batch")


@pytest.mark.asyncio
async def test_history_and_feedback_are_owner_scoped(enterprise_client):
    client, container = enterprise_client
    svc = EnterpriseService(container.settings)
    task = historical_tasks()[0]
    task["user_id"] = "employee"
    svc.store.put("task", task)
    response = await client.post("/enterprise/messages", headers=auth(container, "student"), json={"text": "漏了", "task_id": task["id"]})
    assert response.status_code == 404
    assert (await client.get("/enterprise/tasks", headers=auth(container, "student"))).json()["data"] == []


@pytest.mark.asyncio
async def test_only_global_admin_can_mine_and_examples_have_no_gold(enterprise_client):
    client, container = enterprise_client
    for user in ("employee", "jwc_admin"):
        assert (await client.post("/enterprise/evolution", headers=auth(container, user))).status_code == 403
        assert (await client.get("/enterprise/evolution", headers=auth(container, user))).status_code == 403
    assert (await client.get("/enterprise/evolution", headers=auth(container, "admin"))).json()["data"]["cycles"] == []
    response = await client.get("/enterprise/examples", headers=auth(container, "employee"))
    assert response.status_code == 200
    assert len(response.json()["data"]) == 3
    assert "gold" not in response.text and "test" not in response.text


@pytest.mark.asyncio
async def test_missing_key_and_invalid_environment_fail_explicitly(enterprise_client):
    client, container = enterprise_client
    value = dataset()[0]["task"]["input"]
    value["environment"] = {}
    assert (await client.post("/enterprise/tasks", headers=auth(container, "employee"), json=value)).status_code == 422
    container.settings.openrouter_api_key = ""
    assert (await client.post("/enterprise/messages", headers=auth(container, "employee"), json={"text": "你好"})).status_code == 503


@pytest.mark.asyncio
async def test_external_fixture_import_removed_from_runtime(enterprise_client):
    client, container = enterprise_client
    first = await client.post("/enterprise/demo-history", headers=auth(container, "admin"))
    second = await client.post("/enterprise/demo-history", headers=auth(container, "admin"))
    assert first.status_code == 404 and second.status_code == 404
    svc = EnterpriseService(container.settings)
    assert len(svc.store.records("task")) == 0
    assert not svc.store.records("batch")


@pytest.mark.asyncio
async def test_arbitrary_outcome_labels_cannot_drive_internal_promotion(enterprise_client):
    client, container = enterprise_client
    task = historical_tasks()[0]
    EnterpriseService(container.settings).store.put("task", task)
    payload = {"skill_id": "invented", "success": True, "evidence": "声明成功"}
    assert (await client.post(f"/enterprise/tasks/{task['id']}/outcome", headers=auth(container, "admin"), json=payload)).status_code == 404
    assert (await client.post(f"/enterprise/tasks/{task['id']}/outcome", headers=auth(container, "employee"), json=payload)).status_code == 404
