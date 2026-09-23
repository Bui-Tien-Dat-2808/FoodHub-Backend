import asyncio
import logging
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/live", status_code=status.HTTP_200_OK)
async def liveness_probe():
    """
    Liveness Probe (Kubernetes/Docker healthcheck):
    Kiểm tra process Uvicorn còn đang sống và xử lý được request hay không.
    """
    return {
        "status": "alive",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
    }


@router.get("/ready")
async def readiness_probe(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
):
    """
    Readiness Probe:
    Kiểm tra chuyên sâu các phụ thuộc hạ tầng (PostgreSQL & Redis).
    - 200 OK: Mọi dịch vụ sẵn sàng tiếp nhận traffic
    - 503 Service Unavailable: Một trong các dịch vụ hạ tầng bị ngắt kết nối
    """
    db_status = "connected"
    redis_status = "connected"
    is_ready = True

    # 1. Kiểm tra kết nối PostgreSQL
    try:
        await db.execute(text("SELECT 1"))
        await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=2.0)
    except Exception as exc:
        logger.error("Readiness check DB error: %s", exc)
        db_status = f"unhealthy: {str(exc)}"
        is_ready = False

    # 2. Kiểm tra kết nối Redis
    try:
        pong = await redis.ping()
        pong = await asyncio.wait_for(redis.ping(), timeout=2.0)
        if not pong:
            redis_status = "unhealthy: no ping response"
            is_ready = False
    except Exception as exc:
        logger.error("Readiness check Redis error: %s", exc)
        redis_status = f"unhealthy: {str(exc)}"
        is_ready = False

    content = {
        "status": "ready" if is_ready else "not_ready",
        "service": settings.PROJECT_NAME,
        "dependencies": {
            "database": db_status,
            "redis": redis_status,
        },
    }

    status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=status_code, content=content)

