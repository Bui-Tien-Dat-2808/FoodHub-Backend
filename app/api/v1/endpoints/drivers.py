import json
from datetime import UTC, datetime
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.database import get_db
from app.core.redis import get_redis
from app.models.driver import DriverAssignment, DriverProfile
from app.models.enums import DriverAssignmentStatus, OrderStatus, UserRole
from app.models.order import Order, OrderStatusHistory
from app.models.user import User
from app.schemas.driver import (
    AutoAssignResponse,
    DriverAssignmentResponse,
    DriverLocationUpdate,
    DriverProfileResponse,
    DriverStatusUpdate,
    NearbyDriverResponse,
)
from app.services.driver_matching_service import (
    auto_assign_nearest_driver,
    find_nearby_available_drivers,
)
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


@router.get("/profile", response_model=DriverProfileResponse)
async def get_driver_profile(
    current_user: Annotated[User, Depends(require_roles([UserRole.DRIVER]))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Lấy thông tin hồ sơ tài xế hiện tại."""
    stmt = select(DriverProfile).where(DriverProfile.user_id == current_user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy hồ sơ tài xế",
        )
    return profile


@router.patch("/status", response_model=DriverProfileResponse)
async def update_driver_status(
    status_in: DriverStatusUpdate,
    current_user: Annotated[User, Depends(require_roles([UserRole.DRIVER]))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Bật/tắt trạng thái sẵn sàng (online/offline) của tài xế."""
    stmt = select(DriverProfile).where(DriverProfile.user_id == current_user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy hồ sơ tài xế",
        )
    profile.is_online = status_in.is_online
    await db.commit()
    await db.refresh(profile)
    return profile


@router.post("/deliveries/{order_id}/accept", response_model=DriverAssignmentResponse)
async def accept_delivery(
    order_id: int,
    current_user: Annotated[User, Depends(require_roles([UserRole.DRIVER]))],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
):
    """
    Tài xế nhận đơn hàng đang chờ giao (READY_FOR_PICKUP).
    - Yêu cầu tài xế đang online.
    - Chống tranh chấp: Đảm bảo đơn chưa được tài xế khác nhận.
    - Cập nhật trạng thái đơn thành DRIVER_ASSIGNED và bắn WebSocket realtime.
    """
    stmt_profile = select(DriverProfile).where(DriverProfile.user_id == current_user.id)
    profile = (await db.execute(stmt_profile)).scalar_one_or_none()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy hồ sơ tài xế",
        )

    if not profile.is_online:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tài xế cần bật trạng thái Online để nhận đơn hàng",
        )

    # 1. Kiểm tra đơn hàng
    stmt_order = select(Order).where(Order.id == order_id)
    order = (await db.execute(stmt_order)).scalar_one_or_none()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy đơn hàng",
        )

    if order.status != OrderStatus.READY_FOR_PICKUP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Không thể nhận đơn hàng ở trạng thái '{order.status.value}'",
        )

    # 2. Kiểm tra xem đơn đã có tài xế nhận chưa
    stmt_existing = select(DriverAssignment).where(
        DriverAssignment.order_id == order_id,
        DriverAssignment.status.in_([
            DriverAssignmentStatus.ACCEPTED,
            DriverAssignmentStatus.ARRIVED_AT_STORE,
            DriverAssignmentStatus.DELIVERING,
        ]),
    )
    existing_assignment = (await db.execute(stmt_existing)).scalar_one_or_none()
    if existing_assignment:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Đơn hàng đã được tài xế khác nhận",
        )

    # 3. Tạo phân công giao hàng & cập nhật trạng thái đơn
    assignment = DriverAssignment(
        order_id=order_id,
        driver_id=profile.id,
        status=DriverAssignmentStatus.ACCEPTED,
    )
    db.add(assignment)

    order.status = OrderStatus.DRIVER_ASSIGNED
    profile.is_busy = True

    history = OrderStatusHistory(
        order_id=order.id,
        from_status=OrderStatus.READY_FOR_PICKUP.value,
        to_status=OrderStatus.DRIVER_ASSIGNED.value,
        changed_by_user_id=current_user.id,
        reason="Tài xế nhận đơn giao",
    )
    db.add(history)
    await db.commit()
    await db.refresh(assignment)

    # 4. Bắn WebSocket thông báo trạng thái đơn hàng đã đổi
    await ws_manager.publish(
        redis=redis,
        channel=f"orders:{order.id}",
        message={
            "event": "ORDER_STATUS_CHANGED",
            "order_id": order.id,
            "old_status": OrderStatus.READY_FOR_PICKUP.value,
            "new_status": OrderStatus.DRIVER_ASSIGNED.value,
            "driver_id": current_user.id,
        },
    )

    return assignment


@router.get("/nearby", response_model=list[NearbyDriverResponse])
async def get_nearby_available_drivers(
    lat: Annotated[float, Query(..., ge=-90.0, le=90.0, description="Vĩ độ mục tiêu")],
    lng: Annotated[float, Query(..., ge=-180.0, le=180.0, description="Kinh độ mục tiêu")],
    current_user: Annotated[User, Depends(require_roles([UserRole.ADMIN, UserRole.MERCHANT]))],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    radius_km: float = Query(5.0, ge=0.5, le=50.0, description="Bán kính tìm kiếm (km)"),
    limit: int = Query(5, ge=1, le=20, description="Số lượng tài xế tối đa"),
):
    """
    Tìm danh sách tài xế đang online, rảnh rỗi quanh một toạ độ (nhà hàng hoặc địa chỉ).
    Chỉ dành cho ADMIN hoặc MERCHANT.
    """
    candidates = await find_nearby_available_drivers(
        db=db,
        redis=redis,
        target_lat=lat,
        target_lng=lng,
        max_radius_km=radius_km,
        limit=limit,
    )
    return candidates


@router.post("/auto-assign/{order_id}", response_model=AutoAssignResponse)
async def auto_assign_driver(
    order_id: int,
    current_user: Annotated[User, Depends(require_roles([UserRole.ADMIN, UserRole.MERCHANT]))],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    radius_km: float = Query(5.0, ge=0.5, le=50.0, description="Bán kính tìm kiếm tài xế (km)"),
):
    """
    Tự động tìm và gán tài xế gần nhất trong bán kính cho đơn hàng READY_FOR_PICKUP.
    Dành cho ADMIN hoặc MERCHANT sở hữu nhà hàng của đơn hàng.
    """
    result = await auto_assign_nearest_driver(
        db=db,
        redis=redis,
        order_id=order_id,
        actor_user=current_user,
        max_radius_km=radius_km,
    )
    return result
