"""Readiness of actual enterprise persistence."""
from fastapi import APIRouter, Request
router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request):
    with request.app.state.container.enterprise.store.connection() as db:
        db.execute("SELECT 1").fetchone()
    return {"status": "ready", "runtime": "enterprise",
            "model_configured": bool(request.app.state.container.settings.openrouter_api_key)}
