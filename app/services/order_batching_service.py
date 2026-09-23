import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.driver import BatchWaypoint, DeliveryBatch, DriverAssignment, DriverProfile
from app.models.enums import (
    BatchStatus,
    DriverAssignmentStatus,
    OrderStatus,
    UserRole,
    WaypointType,
)
from app.models.order import Order, OrderStatusHistory
from app.models.restaurant import Restaurant
from app.models.user import User
from app.services.geo_service import haversine_distance
from app.services.ledger_service import record_order_settlement
from app.services.report_service import invalidate_revenue_report_cache
from app.services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)


async def find_batch_candidates(
    db: AsyncSession,
    max_dropoff_distance_km: float = 3.0,
) -> list[dict[str, Any]]:
    """
    Quét tìm các cụm đơn hàng có thể ghép (Batching Candidates):
    - Đơn đang ở trạng thái READY_FOR_PICKUP.
    - Chưa có DriverAssignment đang hoạt động.
    - Chưa thuộc DeliveryBatch nào đang active (PENDING, ASSIGNED, IN_PROGRESS).
    - Cùng xuất phát từ một nhà hàng (Same-Restaurant).
    - Khoảng cách giữa các điểm trả hàng <= max_dropoff_distance_km.
    """
    # 1. Tìm các đơn READY_FOR_PICKUP chưa được gán tài xế
    assigned_orders_subquery = select(DriverAssignment.order_id).where(
        DriverAssignment.status.in_([
            DriverAssignmentStatus.ACCEPTED,
            DriverAssignmentStatus.ARRIVED_AT_STORE,
            DriverAssignmentStatus.DELIVERING,
        ])
    )

    active_batches_subquery = (
        select(BatchWaypoint.order_id)
        .join(DeliveryBatch, BatchWaypoint.batch_id == DeliveryBatch.id)
        .where(DeliveryBatch.status.in_([
            BatchStatus.PENDING,
            BatchStatus.ASSIGNED,
            BatchStatus.IN_PROGRESS,
        ]))
    )

    stmt = (
        select(Order)
        .options(selectinload(Order.restaurant))
        .where(
            Order.status == OrderStatus.READY_FOR_PICKUP,
            Order.id.not_in(assigned_orders_subquery),
            Order.id.not_in(active_batches_subquery),
        )
        .order_by(Order.restaurant_id, Order.created_at)
    )
    orders = (await db.execute(stmt)).scalars().all()

    # 2. Gom nhóm theo restaurant_id
    grouped_by_restaurant: dict[int, list[Order]] = {}
    for order in orders:
        grouped_by_restaurant.setdefault(order.restaurant_id, []).append(order)

    candidates: list[dict[str, Any]] = []

    # 3. Phân tích từng nhóm nhà hàng
    for rest_id, rest_orders in grouped_by_restaurant.items():
        if len(rest_orders) < 2:
            continue

        restaurant = rest_orders[0].restaurant
        rest_lat = restaurant.latitude
        rest_lng = restaurant.longitude

        # Xét các cặp (O1, O2)
        n = len(rest_orders)
        for i in range(n):
            for j in range(i + 1, min(i + 3, n)):
                o1 = rest_orders[i]
                o2 = rest_orders[j]

                # Khoảng cách giữa 2 điểm giao
                dropoff_dist = haversine_distance(
                    o1.delivery_lat, o1.delivery_lng,
                    o2.delivery_lat, o2.delivery_lng,
                )

                if dropoff_dist <= max_dropoff_distance_km:
                    d_r1 = haversine_distance(rest_lat, rest_lng, o1.delivery_lat, o1.delivery_lng)
                    d_r2 = haversine_distance(rest_lat, rest_lng, o2.delivery_lat, o2.delivery_lng)

                    # Giao riêng lẻ 2 chuyến
                    independent_dist = round(d_r1 + d_r2, 2)

                    # Lộ trình ghép tối ưu: Quán -> Điểm gần hơn -> Điểm xa hơn
                    d_near = min(d_r1, d_r2)
                    batched_dist = round(d_near + dropoff_dist, 2)

                    saved_dist = max(0.0, round(independent_dist - batched_dist, 2))
                    efficiency_pct = round((saved_dist / independent_dist * 100.0), 1) if independent_dist > 0 else 0.0

                    candidates.append({
                        "restaurant_id": rest_id,
                        "restaurant_name": restaurant.name,
                        "order_ids": [o1.id, o2.id],
                        "total_batched_distance_km": batched_dist,
                        "independent_distance_km": independent_dist,
                        "saved_distance_km": saved_dist,
                        "efficiency_savings_pct": efficiency_pct,
                        "dropoff_distance_km": round(dropoff_dist, 2),
                    })

    candidates.sort(key=lambda x: x["efficiency_savings_pct"], reverse=True)
    return candidates


async def create_delivery_batch(
    db: AsyncSession,
    restaurant_id: int,
    order_ids: list[int],
    actor_user: User,
) -> DeliveryBatch:
    """
    Tạo một Batch giao hàng mới từ danh sách order_ids:
    - Kiểm tra thẩm quyền (Merchant sở hữu quán hoặc Admin).
    - Kiểm tra tất cả đơn cùng thuộc restaurant_id, đang READY_FOR_PICKUP và chưa bị nhận.
    - Sắp xếp Waypoints tuần tự: Pickups tại quán trước -> Dropoff điểm gần trước -> Dropoff điểm xa sau.
    """
    if len(order_ids) < 2 or len(order_ids) > 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Một batch chỉ được phép ghép từ 2 đến 3 đơn hàng",
        )

    # 1. Kiểm tra nhà hàng
    restaurant = (
        await db.execute(select(Restaurant).where(Restaurant.id == restaurant_id))
    ).scalar_one_or_none()
    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy nhà hàng",
        )

    # 2. Phân quyền
    if actor_user.role == UserRole.MERCHANT and restaurant.owner_id != actor_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền tạo batch cho nhà hàng của người khác",
        )
    elif actor_user.role not in [UserRole.MERCHANT, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Chủ quán hoặc Quản trị viên mới có quyền tạo batch giao hàng",
        )

    # 3. Lấy và kiểm tra các đơn hàng
    stmt_orders = (
        select(Order)
        .where(Order.id.in_(order_ids))
        .with_for_update()
    )
    orders = (await db.execute(stmt_orders)).scalars().all()

    if len(orders) != len(order_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Một số đơn hàng không tồn tại trong hệ thống",
        )

    for o in orders:
        if o.restaurant_id != restaurant_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Đơn hàng #{o.id} không thuộc nhà hàng #{restaurant_id}",
            )
        if o.status != OrderStatus.READY_FOR_PICKUP:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Đơn hàng #{o.id} đang ở trạng thái '{o.status.value}', yêu cầu 'READY_FOR_PICKUP'",
            )

    # 4. Kiểm tra xem có đơn nào đã có tài xế nhận hoặc nằm trong batch active không
    stmt_existing_assign = select(DriverAssignment).where(
        DriverAssignment.order_id.in_(order_ids),
        DriverAssignment.status.in_([
            DriverAssignmentStatus.ACCEPTED,
            DriverAssignmentStatus.ARRIVED_AT_STORE,
            DriverAssignmentStatus.DELIVERING,
        ]),
    )
    if (await db.execute(stmt_existing_assign)).scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Một trong các đơn hàng đã có tài xế nhận giao",
        )

    stmt_existing_batch = (
        select(BatchWaypoint)
        .join(DeliveryBatch, BatchWaypoint.batch_id == DeliveryBatch.id)
        .where(
            BatchWaypoint.order_id.in_(order_ids),
            DeliveryBatch.status.in_([
                BatchStatus.PENDING,
                BatchStatus.ASSIGNED,
                BatchStatus.IN_PROGRESS,
            ]),
        )
    )
    if (await db.execute(stmt_existing_batch)).scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Một trong các đơn hàng đã được ghép vào batch khác đang hoạt động",
        )

    # 5. Sắp xếp Waypoints tuần tự (Waypoints Sequencing)
    # Tính khoảng cách từ quán đến từng điểm trả hàng
    orders_with_dist = []
    for o in orders:
        d = haversine_distance(
            restaurant.latitude, restaurant.longitude,
            o.delivery_lat, o.delivery_lng,
        )
        orders_with_dist.append((o, d))

    # Sắp xếp điểm giao từ gần đến xa
    orders_with_dist.sort(key=lambda item: item[1])

    batch_code = f"BATCH-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:6].upper()}"
    batch = DeliveryBatch(
        batch_code=batch_code,
        restaurant_id=restaurant_id,
        driver_id=None,
        status=BatchStatus.PENDING,
        total_distance_km=0.0,
    )
    db.add(batch)
    await db.flush()

    # Tạo các điểm PICKUP trước (1 chặng pickup cho mỗi đơn tại quán)
    waypoints: list[BatchWaypoint] = []
    seq = 1
    for o, _ in orders_with_dist:
        wp = BatchWaypoint(
            batch_id=batch.id,
            order_id=o.id,
            sequence=seq,
            waypoint_type=WaypointType.PICKUP,
            target_lat=restaurant.latitude,
            target_lng=restaurant.longitude,
            target_address=restaurant.address,
            is_completed=False,
        )
        db.add(wp)
        waypoints.append(wp)
        seq += 1

    # Tạo các điểm DROPOFF tiếp theo (theo thứ tự cự ly tăng dần)
    prev_lat = restaurant.latitude
    prev_lng = restaurant.longitude
    total_dist = 0.0

    for o, _ in orders_with_dist:
        seg_dist = haversine_distance(prev_lat, prev_lng, o.delivery_lat, o.delivery_lng)
        total_dist += seg_dist
        prev_lat = o.delivery_lat
        prev_lng = o.delivery_lng

        wp = BatchWaypoint(
            batch_id=batch.id,
            order_id=o.id,
            sequence=seq,
            waypoint_type=WaypointType.DROPOFF,
            target_lat=o.delivery_lat,
            target_lng=o.delivery_lng,
            target_address=o.delivery_address,
            is_completed=False,
        )
        db.add(wp)
        waypoints.append(wp)
        seq += 1

    batch.total_distance_km = round(total_dist, 2)
    await db.commit()

    # Nạp lại kèm waypoints
    stmt_reload = (
        select(DeliveryBatch)
        .options(selectinload(DeliveryBatch.waypoints))
        .where(DeliveryBatch.id == batch.id)
    )
    return (await db.execute(stmt_reload)).scalar_one()


async def driver_accept_batch(
    db: AsyncSession,
    redis: aioredis.Redis,
    batch_id: int,
    driver_user: User,
) -> DeliveryBatch:
    """
    Tài xế nhận trọn vẹn một batch giao hàng:
    - Kiểm tra tài xế online và chưa bận.
    - Chống tranh chấp: Lock batch bằng with_for_update().
    - Chuyển batch sang ASSIGNED.
    - Chuyển tất cả đơn trong batch sang DRIVER_ASSIGNED và tạo DriverAssignment có batch_id.
    - Đặt driver is_busy = True.
    - Phát WebSocket realtime cho từng đơn và admin live-ops.
    """
    # 1. Tìm hồ sơ tài xế
    stmt_profile = select(DriverProfile).where(DriverProfile.user_id == driver_user.id).with_for_update()
    profile = (await db.execute(stmt_profile)).scalar_one_or_none()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy hồ sơ tài xế",
        )

    if not profile.is_online:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tài xế cần bật trạng thái Online để nhận chuyến giao",
        )

    if profile.is_busy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tài xế đang bận thực hiện đơn hàng khác, không thể nhận thêm batch",
        )

    # 2. Tìm và khóa batch
    stmt_batch = (
        select(DeliveryBatch)
        .options(selectinload(DeliveryBatch.waypoints))
        .where(DeliveryBatch.id == batch_id)
        .with_for_update()
    )
    batch = (await db.execute(stmt_batch)).scalar_one_or_none()
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy batch giao hàng",
        )

    if batch.status != BatchStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Batch đã ở trạng thái '{batch.status.value}', không thể nhận",
        )

    # 3. Cập nhật batch và tài xế
    batch.driver_id = profile.id
    batch.status = BatchStatus.ASSIGNED
    profile.is_busy = True

    # 4. Cập nhật tất cả các đơn hàng trong batch
    order_ids = list({wp.order_id for wp in batch.waypoints})
    stmt_orders = select(Order).where(Order.id.in_(order_ids)).with_for_update()
    orders = (await db.execute(stmt_orders)).scalars().all()

    for order in orders:
        if order.status != OrderStatus.READY_FOR_PICKUP:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Đơn hàng #{order.id} không còn ở trạng thái sẵn sàng (hiện tại: {order.status.value})",
            )

        assignment = DriverAssignment(
            order_id=order.id,
            driver_id=profile.id,
            batch_id=batch.id,
            status=DriverAssignmentStatus.ACCEPTED,
        )
        db.add(assignment)

        order.status = OrderStatus.DRIVER_ASSIGNED
        history = OrderStatusHistory(
            order_id=order.id,
            from_status=OrderStatus.READY_FOR_PICKUP.value,
            to_status=OrderStatus.DRIVER_ASSIGNED.value,
            changed_by_user_id=driver_user.id,
            reason=f"Tài xế nhận batch giao #{batch.batch_code}",
        )
        db.add(history)

    await db.commit()

    # 5. Phát WebSocket thông báo
    for order in orders:
        await ws_manager.publish(
            redis=redis,
            channel=f"orders:{order.id}",
            message={
                "event": "ORDER_STATUS_CHANGED",
                "order_id": order.id,
                "old_status": OrderStatus.READY_FOR_PICKUP.value,
                "new_status": OrderStatus.DRIVER_ASSIGNED.value,
                "driver_id": driver_user.id,
                "batch_code": batch.batch_code,
            },
        )

    await ws_manager.broadcast_admin_live_ops(
        redis=redis,
        message={
            "event": "BATCH_ASSIGNED",
            "batch_id": batch.id,
            "batch_code": batch.batch_code,
            "driver_id": profile.id,
            "orders_count": len(orders),
        },
    )

    return batch


async def progress_batch_waypoint(
    db: AsyncSession,
    redis: aioredis.Redis,
    batch_id: int,
    waypoint_id: int,
    driver_user: User,
) -> dict[str, Any]:
    """
    Tài xế hoàn thành chặng dừng (Waypoint):
    - Kiểm tra quyền sở hữu batch của tài xế.
    - Kiểm tra tính tuần tự: Các chặng trước phải hoàn thành xong trước.
    - Nếu waypoint là PICKUP:
        + Đơn hàng chuyển sang PICKED_UP.
        + Assignment tương ứng chuyển sang DELIVERING.
    - Nếu waypoint là DROPOFF:
        + Đơn hàng chuyển sang DELIVERED.
        + Assignment tương ứng chuyển sang COMPLETED.
        + Hạch toán sổ cái ghi kép (Double-Entry Ledger).
        + Xoá cache doanh thu quán.
    - Nếu tất cả các waypoints đã hoàn thành:
        + Batch chuyển sang COMPLETED.
        + Tài xế được giải phóng: is_busy = False.
    """
    # 1. Tìm hồ sơ tài xế
    stmt_profile = select(DriverProfile).where(DriverProfile.user_id == driver_user.id)
    profile = (await db.execute(stmt_profile)).scalar_one_or_none()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy hồ sơ tài xế",
        )

    # 2. Tìm batch
    stmt_batch = (
        select(DeliveryBatch)
        .options(selectinload(DeliveryBatch.waypoints))
        .where(DeliveryBatch.id == batch_id)
        .with_for_update()
    )
    batch = (await db.execute(stmt_batch)).scalar_one_or_none()
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy batch giao hàng",
        )

    if batch.driver_id != profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không phải là tài xế phụ trách batch giao hàng này",
        )

    if batch.status not in [BatchStatus.ASSIGNED, BatchStatus.IN_PROGRESS]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Batch đang ở trạng thái '{batch.status.value}', không thể cập nhật chặng dừng",
        )

    # 3. Tìm waypoint mục tiêu
    target_wp: BatchWaypoint | None = None
    for wp in batch.waypoints:
        if wp.id == waypoint_id:
            target_wp = wp
            break

    if not target_wp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy chặng dừng trong batch này",
        )

    if target_wp.is_completed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Chặng dừng này đã được hoàn thành trước đó",
        )

    # 4. Kiểm tra tính tuần tự: Các chặng trước đó phải đã hoàn thành
    for wp in batch.waypoints:
        if wp.sequence < target_wp.sequence and not wp.is_completed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Vui lòng hoàn thành chặng #{wp.sequence} ({wp.waypoint_type.value}) trước khi thực hiện chặng này",
            )

    # 5. Đánh dấu hoàn thành waypoint
    target_wp.is_completed = True
    target_wp.completed_at = datetime.now(UTC)
    batch.status = BatchStatus.IN_PROGRESS

    # 6. Tìm đơn hàng và assignment tương ứng
    stmt_order = select(Order).where(Order.id == target_wp.order_id).with_for_update()
    order = (await db.execute(stmt_order)).scalar_one()

    stmt_assignment = select(DriverAssignment).where(
        DriverAssignment.order_id == target_wp.order_id,
        DriverAssignment.driver_id == profile.id,
    ).with_for_update()
    assignment = (await db.execute(stmt_assignment)).scalar_one()

    new_order_status: OrderStatus
    if target_wp.waypoint_type == WaypointType.PICKUP:
        new_order_status = OrderStatus.PICKED_UP
        assignment.status = DriverAssignmentStatus.DELIVERING
        reason = f"Tài xế đã lấy món tại quán #{order.restaurant_id} (chặng #{target_wp.sequence})"
    else:
        new_order_status = OrderStatus.DELIVERED
        assignment.status = DriverAssignmentStatus.COMPLETED
        assignment.delivered_at = datetime.now(UTC)
        reason = f"Tài xế đã giao hàng thành công tới khách (chặng #{target_wp.sequence})"

    old_status = order.status
    order.status = new_order_status

    history = OrderStatusHistory(
        order_id=order.id,
        from_status=old_status.value,
        to_status=new_order_status.value,
        changed_by_user_id=driver_user.id,
        reason=reason,
    )
    db.add(history)

    # Nếu đơn giao thành công -> Quyết toán sổ cái kép & xoá cache doanh thu
    if new_order_status == OrderStatus.DELIVERED:
        await record_order_settlement(db, order)
        await invalidate_revenue_report_cache(redis, order.restaurant_id)

    # 7. Kiểm tra xem toàn bộ các chặng trong batch đã xong chưa
    all_completed = all(wp.is_completed for wp in batch.waypoints)
    if all_completed:
        batch.status = BatchStatus.COMPLETED
        batch.completed_at = datetime.now(UTC)
        profile.is_busy = False

    await db.commit()

    # 8. Bắn WebSocket realtime
    await ws_manager.publish(
        redis=redis,
        channel=f"orders:{order.id}",
        message={
            "event": "ORDER_STATUS_CHANGED",
            "order_id": order.id,
            "old_status": old_status.value,
            "new_status": new_order_status.value,
            "driver_id": driver_user.id,
            "waypoint_type": target_wp.waypoint_type.value,
        },
    )

    if all_completed:
        await ws_manager.broadcast_admin_live_ops(
            redis=redis,
            message={
                "event": "BATCH_COMPLETED",
                "batch_id": batch.id,
                "batch_code": batch.batch_code,
                "driver_id": profile.id,
            },
        )

    return {
        "batch_id": batch.id,
        "waypoint_id": target_wp.id,
        "waypoint_type": target_wp.waypoint_type.value,
        "order_id": order.id,
        "order_status": new_order_status.value,
        "batch_status": batch.status.value,
        "is_batch_completed": all_completed,
        "message": f"Hoàn thành chặng #{target_wp.sequence} ({target_wp.waypoint_type.value}) thành công",
    }


async def get_driver_current_batch(
    db: AsyncSession,
    driver_user: User,
) -> DeliveryBatch | None:
    """
    Lấy batch giao hàng mà tài xế hiện tại đang phụ trách (ASSIGNED hoặc IN_PROGRESS).
    """
    stmt_profile = select(DriverProfile).where(DriverProfile.user_id == driver_user.id)
    profile = (await db.execute(stmt_profile)).scalar_one_or_none()
    if not profile:
        return None

    stmt_batch = (
        select(DeliveryBatch)
        .options(selectinload(DeliveryBatch.waypoints))
        .where(
            DeliveryBatch.driver_id == profile.id,
            DeliveryBatch.status.in_([BatchStatus.ASSIGNED, BatchStatus.IN_PROGRESS]),
        )
        .order_by(DeliveryBatch.created_at.desc())
    )
    return (await db.execute(stmt_batch)).scalar_one_or_none()
