from fastapi import APIRouter
from src.core.config import get_settings, now_tz

router = APIRouter(prefix="/health", tags=["Health"])
settings = get_settings()


@router.get("")
async def health_check():
    return {
        "status": "ok",
        "environment": settings.ENVIRONMENT,
        "timestamp": now_tz().isoformat(),
    }

