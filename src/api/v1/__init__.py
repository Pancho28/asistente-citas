from fastapi import APIRouter
from src.api.v1.health import router as health_router
from src.api.v1.webhook import router as webhook_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health_router)
api_v1_router.include_router(webhook_router)

__all__ = ["api_v1_router"]

