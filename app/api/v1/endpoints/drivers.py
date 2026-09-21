import json
from datetime import UTC, datetime
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.database import get_db
from app.core.redis import get_redis
from app.models.driver import DriverAssignment, DriverProfile
from app.models.enums import DriverAssignmentStatus, UserRole
from app.models.user import User
from app.schemas.driver import DriverLocationUpdate
from app.services.websocket_manager import ws_manager

router = APIRouter()

@router.post("/location", status_code=status.HTTP_200_OK)
async def update_driver_location(
    data: DriverLocationUpdate,
    current_user: Annotated[User, Depends(require_roles([UserRole.DRIVER]))],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)]
):
    """
        Cập nhật vị trí GPS của tài xế:
        1. Lưu cache Redis
        2. Cập nhật toạ độ trong DB
        3. Phát WebSocket thông báo tới các đơn hàng tài xế đang giao
    """

    # 1. Lưu vị trí vào cache Redis
    location_payload = {
        "latitude": data.latitude,
        "longitude": data.longitude,
        "updated_at": datetime.now(UTC).isoformat()
    }
    await redis.set(
        f"driver:{current_user.id}:location",
        json.dumps(location_payload),
        ex=300
    )

    # 2. Tìm hồ sơ tài xế và cập nhật toạ độ vào DB
    stmt_profile = select(DriverProfile).where(DriverProfile.user_id == current_user.id)
    profile = (await db.execute(stmt_profile)).scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy tài xế"
        )

    profile.current_lat = data.latitude
    profile.current_lng = data.longitude
    await db.commit()

    # 3. Tìm các đơn hàng mà tài xế này đang phụ trách
    active_statuses = [
        DriverAssignmentStatus.ACCEPTED,
        DriverAssignmentStatus.ARRIVED_AT_STORE,
        DriverAssignmentStatus.DELIVERING
    ]
    stmt_assignments = select(DriverAssignment).where(
        DriverAssignment.driver_id == profile.id,
        DriverAssignment.status.in_(active_statuses),
    )
    assignments = (await db.execute(stmt_assignments)).scalars().all()

    # 4. Bắn toạ độ qua WebSocket channel của từng đơn hàng
    for assignment in assignments:
        await ws_manager.publish(
            redis=redis,
            channel=f"orders:{assignment.order_id}",
            message={
                "event": "DRIVER_LOCATION_UPDATED",
                "order_id": assignment.order_id,
                "driver_id": current_user.id,
                "latitude": data.latitude,
                "longitude": data.longitude
            }
        )

    return {
        "status": "success",
        "message": "Cập nhật vị trí thành công",
        "active_orders": len(assignments)
    }