import json
import logging
from typing import Any

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.driver import DriverAssignment, DriverProfile
from app.models.enums import DriverAssignmentStatus, OrderStatus, UserRole
from app.models.order import Order, OrderStatusHistory
from app.models.user import User
from app.services.geo_service import haversine_distance
from app.services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)


async def get_driver_live_coordinates(
    profile: DriverProfile,
    redis: aioredis.Redis,
) -> tuple[float, float] | None:
    """
    Lấy tọa độ realtime của tài xế:
    1. Đọc từ Redis cache: driver:{user_id}:location
    2. Nếu không có hoặc lỗi parse, fallback về DB (current_lat, current_lng)
    """
    try:
        raw_data = await redis.get(f"driver:{profile.user_id}:location")
        if raw_data:
            data = json.loads(raw_data)
            lat = float(data.get("latitude"))
            lng = float(data.get("longitude"))
            return lat, lng
    except Exception as exc:
        logger.warning("Lỗi đọc vị trí driver từ Redis: %s", exc)

    if profile.current_lat is not None and profile.current_lng is not None:
        return float(profile.current_lat), float(profile.current_lng)

    return None


async def find_nearby_available_drivers(
    db: AsyncSession,
    redis: aioredis.Redis,
    target_lat: float,
    target_lng: float,
    max_radius_km: float = 5.0,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """
    Tìm danh sách tài xế đang online, không bận và nằm trong bán kính max_radius_km.
    Kết quả được sắp xếp tăng dần theo khoảng cách (km).
    """
    stmt = (
        select(DriverProfile, User)
        .join(User, DriverProfile.user_id == User.id)
        .where(
            DriverProfile.is_online.is_(True),
            DriverProfile.is_busy.is_(False),
            User.is_active.is_(True),
        )
    )
    rows = (await db.execute(stmt)).all()

    candidates: list[dict[str, Any]] = []
    for profile, user in rows:
        coords = await get_driver_live_coordinates(profile, redis)
        if not coords:
            continue

        lat, lng = coords
        dist = haversine_distance(target_lat, target_lng, lat, lng)
        if dist <= max_radius_km:
            candidates.append({
                "driver_id": profile.id,
                "user_id": user.id,
                "full_name": user.full_name,
                "phone_number": user.phone,
                "license_plate": profile.license_plate,
                "distance_km": dist,
                "current_lat": lat,
                "current_lng": lng,
                "rating": profile.rating,
            })

    candidates.sort(key=lambda x: x["distance_km"])
    return candidates[:limit]


async def auto_assign_nearest_driver(
    db: AsyncSession,
    redis: aioredis.Redis,
    order_id: int,
    actor_user: User,
    max_radius_km: float = 5.0,
) -> dict[str, Any]:
    """
    Tự động tìm và gán tài xế gần nhất trong bán kính cho đơn hàng.
    - Phân quyền: ADMIN hoặc MERCHANT sở hữu quán có đơn này.
    - Kiểm tra trạng thái đơn: Phải là READY_FOR_PICKUP.
    - Kiểm tra đơn chưa được tài xế nhận.
    - Cập nhật atomic: Gán tài xế, đổi trạng thái đơn sang DRIVER_ASSIGNED, đánh dấu tài xế bận.
    - Phát thông báo WebSocket realtime.
    """
    # 1. Tìm đơn hàng kèm nhà hàng
    stmt_order = (
        select(Order)
        .options(selectinload(Order.restaurant))
        .where(Order.id == order_id)
    )
    order = (await db.execute(stmt_order)).scalar_one_or_none()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy đơn hàng",
        )

    # 2. Kiểm tra phân quyền
    if actor_user.role == UserRole.MERCHANT:
        if not order.restaurant or order.restaurant.owner_id != actor_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn không có quyền thao tác trên đơn hàng của nhà hàng này",
            )
    elif actor_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Quản trị viên hoặc Chủ quán mới có quyền tự động gán tài xế",
        )

    # 3. Kiểm tra xem đơn đã được gán tài xế chưa
    stmt_assign = select(DriverAssignment).where(
        DriverAssignment.order_id == order.id,
        DriverAssignment.status.in_([
            DriverAssignmentStatus.ACCEPTED,
            DriverAssignmentStatus.ARRIVED_AT_STORE,
            DriverAssignmentStatus.DELIVERING,
        ]),
    )
    existing = (await db.execute(stmt_assign)).scalar_one_or_none()
    if existing or order.status == OrderStatus.DRIVER_ASSIGNED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Đơn hàng đã có tài xế nhận giao",
        )

    # 4. Kiểm tra trạng thái đơn hàng
    if order.status != OrderStatus.READY_FOR_PICKUP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Đơn hàng ở trạng thái '{order.status.value}' không thể gán tài xế (yêu cầu 'READY_FOR_PICKUP')",
        )

    # 5. Tìm tài xế khả dụng gần nhà hàng nhất
    rest_lat = order.restaurant.latitude
    rest_lng = order.restaurant.longitude
    candidates = await find_nearby_available_drivers(
        db=db,
        redis=redis,
        target_lat=rest_lat,
        target_lng=rest_lng,
        max_radius_km=max_radius_km,
        limit=3,
    )

    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy tài xế nào đang online và sẵn sàng trong bán kính {max_radius_km} km",
        )

    # Chọn candidate gần nhất
    best_candidate = candidates[0]
    best_driver_id = best_candidate["driver_id"]

    stmt_driver = select(DriverProfile).where(DriverProfile.id == best_driver_id).with_for_update()
    profile = (await db.execute(stmt_driver)).scalar_one_or_none()

    if not profile or not profile.is_online or profile.is_busy:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tài xế đã bận hoặc offline trong lúc xử lý, vui lòng thử lại",
        )

    # 6. Gán tài xế và cập nhật trạng thái đơn
    assignment = DriverAssignment(
        order_id=order.id,
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
        changed_by_user_id=actor_user.id,
        reason=f"Tự động gán tài xế {best_candidate['full_name']} (cách {best_candidate['distance_km']} km)",
    )
    db.add(history)

    await db.commit()

    # 7. Phát WebSocket realtime
    await ws_manager.publish(
        redis=redis,
        channel=f"orders:{order.id}",
        message={
            "event": "ORDER_STATUS_CHANGED",
            "order_id": order.id,
            "old_status": OrderStatus.READY_FOR_PICKUP.value,
            "new_status": OrderStatus.DRIVER_ASSIGNED.value,
            "driver_id": profile.user_id,
            "distance_km": best_candidate["distance_km"],
        },
    )

    return {
        "order_id": order.id,
        "driver_id": profile.id,
        "driver_name": best_candidate["full_name"],
        "license_plate": profile.license_plate,
        "distance_km": best_candidate["distance_km"],
        "status": OrderStatus.DRIVER_ASSIGNED.value,
        "message": f"Tự động gán tài xế {best_candidate['full_name']} thành công (khoảng cách {best_candidate['distance_km']} km)",
    }
