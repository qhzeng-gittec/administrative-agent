"""Enterprise task execution and autonomous internal evolution service."""
import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import generate_latest
from starlette.responses import Response
from app.api.router import api_router, root_router
from app.config import get_settings
from app.enterprise.runtime import build_runtime
from app.utils.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    app.state.container = await build_runtime(settings)
    service = app.state.container.enterprise
    workers = [asyncio.create_task(service.worker(kind)) for kind in ("classify", "execute", "mine", "observe")]
    workers.append(asyncio.create_task(service.scheduler()))
    try:
        yield
    finally:
        for worker in workers:
            worker.cancel()
        for worker in workers:
            with suppress(asyncio.CancelledError):
                await worker


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list,
                   allow_credentials="*" not in settings.cors_origin_list,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(root_router)
app.include_router(api_router)


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type="text/plain; version=0.0.4")


@app.get("/")
async def index():
    return {"app": settings.app_name, "docs": "/docs", "health": "/healthz"}
