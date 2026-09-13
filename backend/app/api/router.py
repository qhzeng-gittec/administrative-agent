"""Enterprise routes; historical benchmark modules have no runtime entry."""
from fastapi import APIRouter
from app.api.routes import auth, enterprise, health

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(enterprise.router)
root_router = APIRouter()
root_router.include_router(health.router)
