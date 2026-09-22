import logging
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.rate_limiter import check_rate_limit
from app.core.redis import get_redis
from app.models.enums import OrderStatus, UserRole
from app.models.order import Order, OrderStatusHistory
from app.models.restaurant import MenuItem, Restaurant
from app.models.user import User
from app.schemas.order import OrderCreate, OrderDetailResponse, OrderResponse, OrderStatusUpdate
from app.services.order_service import (
    create_order_transaction,
    get_order_by_idempotency_key,
    list_orders_no_n_plus_one,
)
from app.services.order_state_machine import validate_state_transition
from app.services.report_service import invalidate_revenue_report_cache
from app.services.websocket_manager import ws_manager
from app.tasks.notification_tasks import send_order_status_notification
from app.tasks.order_tasks import auto_cancel_unpaid_order

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    order_in: OrderCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    x_idempotency_key: Annotated[str | None, Header(alias="X-Idempotency-Key")] = None
):
    """
        Khách hàng tạo đơn hàng trong Transaction được bảo vệ bằng Idempotency Key.
        Nếu cùng 1 key được gửi lại, hệ thống trả về đơn đã tạo trước đó mà không tạo đơn mới.
    """

    await check_rate_limit(
        redis=redis,
        key=f"ratelimit:user:{current_user.id}:create_order",
        max_requests=5,
        window_seconds=60,
    )

    redis_key = None

    if x_idempotency_key:
        redis_key = f"idempotency:order:{current_user.id}:{x_idempotency_key}"

        # 1. Kiểm tra trong Redis
        try:
            cached_val = await redis.get(redis_key)
            if cached_val == "PROCESSING":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Yêu cầu đặt đơn đang được xử lý, vui lòng không gửi lặp lại"
                )
            elif cached_val:
                # Tạo đơn thành công trước đó -> Lấy lại đơn từ DB và trả về
                existing_order_id = int(cached_val)
                stmt = select(Order).options(selectinload(Order.items)).where(Order.id == existing_order_id)
                old_order = (await db.execute(stmt)).scalar_one_or_none()
                if old_order:
                    return old_order
        except aioredis.RedisError:
            # Nếu Redis bị lỗi, fallback kiểm tra trực tiếp trong DB
            old_order= await get_order_by_idempotency_key(db, current_user.id, x_idempotency_key)
            if old_order:
                return old_order

        # 2. Đặt khoá tạm thời "PROCESSING" với TTL 60s
        try:
            await redis.set(redis_key, "PROCESSING", ex=60)
        except aioredis.RedisError:
            pass
    try:
        # 3. Tạo đơn trong DB Transaction
        order = await create_order_transaction(db, current_user.id, order_in, idempotency_key=x_idempotency_key)

        # 4. Tạo thành công -> Cập nhật Redis lưu order_id với TTL 24 tiếng = 86400s
        if redis_key:
            try:
                await redis.set(redis_key, str(order.id), ex=86400)
            except aioredis.RedisError:
                pass

        # 5. Kích hoạt Celery Tasks
        try:
            send_order_status_notification.delay(order.id, order.status.value, current_user.email)
            auto_cancel_unpaid_order.apply_async(args=[order.id], countdown=900)
        except Exception as exc:
            logger.error(f"Không thể đẩy Celery task cho đơn #{order.id}: {exc}")

        # 6. Bắn realtime thông báo đơn mới đến Quán ăn
        try:
            await ws_manager.publish(
                redis=redis,
                channel=f"merchant:{order.restaurant_id}",
                message={
                    "event": "NEW_ORDER",
                    "order_id": order.id,
                    "restaurant_id": order.restaurant_id,
                    "total_amount": float(order.total_amount),
                    "status": order.status.value,
                    "created_at": order.created_at.isoformat(),
                },
            )
        except Exception as exc:
            logger.error(f"Không thể gửi thông báo WebSocket đơn #{order.id}: {exc}")

        return order

    except Exception:
        # Nếu có lỗi, xoá cờ PROCESSING để khách có thể thử lại
        if redis_key:
            try:
                await redis.delete(redis_key)
            except aioredis.RedisError:
                pass
        raise

@router.get("/", response_model=list[OrderResponse])
async def get_my_orders(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100)
):
    """Lấy danh sách đơn hàng đã tối ưu, không bị N+1 query."""
    return await list_orders_no_n_plus_one(db, current_user, skip, limit)

@router.patch("/{order_id}/status", response_model=OrderResponse)
async def update_order_status(
    order_id: int,
    status_in: OrderStatusUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)]
):
    """Cập nhật trạng thái đơn hàng qua State Machine."""
    stmt = select(Order).options(selectinload(Order.items)).where(Order.id == order_id)
    order = (await db.execute(stmt)).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy đơn hàng")

    # Kiểm tra quy tắc chuyển trạng thái & quyền hạn
    validate_state_transition(order.status, status_in.new_status, current_user.role)

    old_status = order.status
    order.status = status_in.new_status

    # Nếu đơn chuyển sang trạng thái CANCELLED -> Hoàn trả tồn kho cho quán
    if status_in.new_status == OrderStatus.CANCELLED:
        for order_item in order.items:
            item = (await db.execute(select(MenuItem).where(MenuItem.id == order_item.menu_item_id))).scalar_one_or_none()
            if item:
                item.stock_quantity += order_item.quantity

    # Ghi log lịch sử
    history = OrderStatusHistory(
        order_id=order.id,
        from_status=old_status.value,
        to_status=status_in.new_status.value,
        changed_by_user_id=current_user.id,
        reason=status_in.reason
    )
    db.add(history)
    await db.commit()

    try:
        customer = (await db.execute(select(User).where(User.id == order.customer_id))).scalar_one_or_none()
        customer_email = customer.email if customer else "customer@foodhub.com"
        send_order_status_notification.delay(order.id, status_in.new_status.value, customer_email)
    except Exception as exc:
        logger.error(f"Không thể gửi thông báo cho đơn #{order.id}: {exc}")

    await ws_manager.publish(
        redis=redis,
        channel=f"orders:{order.id}",
        message={
            "event": "ORDER_STATUS_CHANGED",
            "order_id": order.id,
            "old_status": old_status.value,
            "new_status": status_in.new_status.value,
        },
    )

    # Nếu đơn giao thành công -> Xoá cache báo cáo doanh thu của quán
    if status_in.new_status == OrderStatus.DELIVERED:
        await invalidate_revenue_report_cache(redis, order.restaurant_id)

    await db.refresh(order, attribute_names=["items"])
    return order

@router.get("/{order_id}", response_model=OrderDetailResponse)
async def get_order_detail(
    order_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
): 
    """
        Xem chi tiết đơn hàng + danh sách món + lịch sử FSM
    """
    stmt = (
        select(Order)
        .options(
            selectinload(Order.items),
            selectinload(Order.histories),
        )
        .where(Order.id == order_id)
    )
    order = (await db.execute(stmt)).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy đơn hàng")

    # Phân quyền: Khách chỉ được xem đơn của mình, Quán chỉ được xem đơn của quán mình
    if current_user.role == UserRole.CUSTOMER and order.customer_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bạn không có quyền xem đơn hàng này")
    elif current_user.role == UserRole.MERCHANT:
        restaurant = (await db.execute(select(Restaurant).where(Restaurant.id == order.restaurant_id))).scalar_one_or_none()
        if not restaurant or restaurant.owner_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bạn không có quyền xem đơn hàng này")

    return order