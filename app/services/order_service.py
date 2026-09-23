import math
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.metrics import ORDERS_CREATED_TOTAL
from app.models.enums import DiscountType, OrderStatus, UserRole
from app.models.order import Order, OrderItem, OrderStatusHistory
from app.models.promotion import Voucher, VoucherUsage
from app.models.restaurant import MenuItem, Restaurant
from app.models.user import User
from app.schemas.order import OrderCreate
from app.services.pricing_service import calculate_delivery_fee, compute_dynamic_surge


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
        Tính cự ly (km) bằng công thức Haversine
    """
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    return round(R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 2)

async def create_order_transaction(
    db: AsyncSession,
    customer_id: int,
    order_in: OrderCreate,
    idempotency_key: str | None = None
) -> Order:
    # 1. Kiểm tra nhà hàng
    restaurant = (
        await db.execute(select(Restaurant).where(Restaurant.id == order_in.restaurant_id))
    ).scalar_one_or_none()
    if not restaurant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy nhà hàng")
    if not restaurant.is_open:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nhà hàng hiện đang đóng cửa")

    # 2. Tính khoảng cách và phí ship (kèm Surge Pricing)
    # 2. Tính khoảng cách và phí ship (kèm Surge Pricing & Geofencing)
    distance_km = haversine_distance(restaurant.latitude, restaurant.longitude, order_in.delivery_lat, order_in.delivery_lng)
    max_radius = getattr(restaurant, "delivery_radius_km", 10.0) or 10.0
    if distance_km > max_radius:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Địa chỉ giao hàng ({distance_km} km) vượt quá bán kính phục vụ của nhà hàng (tối đa {max_radius} km)",
        )

    surge_multiplier, _ = await compute_dynamic_surge(db, restaurant.latitude, restaurant.longitude)
    delivery_fee = calculate_delivery_fee(distance_km, surge_multiplier=surge_multiplier)

    # 3. Lấy danh sách món ăn từ DB & kiểm tra tính hợp lệ
    item_map = {
        item.menu_item_id: item.quantity for item in order_in.items
    }
    item_ids = list(item_map.keys())

    stmt = select(MenuItem).where(MenuItem.id.in_(item_ids), MenuItem.restaurant_id == restaurant.id)
    result = await db.execute(stmt)
    menu_items = result.scalars().all()

    if len(menu_items) != len(item_ids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Một số món ăn không thuộc nhà hàng này hoặc không tồn tại")

    # 4. Tính subtotal & trừ tồn kho
    subtotal = 0
    order_items = []

    for item in menu_items:
        qty = item_map[item.id]
        if not item.is_available:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Món '{item.name}' hiện đang tạm ngưng phục vụ")
        if item.stock_quantity < qty:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Món '{item.name}' không đủ số lượng phục vụ")

        stmt_stock = (
            update(MenuItem)
            .where(
                MenuItem.id == item.id,
                MenuItem.stock_quantity >= qty,
                MenuItem.is_available.is_(True)
            )
            .values(stock_quantity=MenuItem.stock_quantity - qty)
            .returning(MenuItem.id, MenuItem.stock_quantity)
            .execution_options(synchronize_session=False)
        )
        updated_stock = (await db.execute(stmt_stock)).first()
        if not updated_stock:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail=f"Món '{item.name}' không đủ số lượng phục vụ (đã hết hàng)"
            )

        item_subtotal = item.base_price * qty
        subtotal += item_subtotal

        order_items.append(OrderItem(
            menu_item_id=item.id,
            name_snapshot=item.name,
            quantity=qty,
            unit_price=item.base_price,
            subtotal=item_subtotal
        ))

    discount_amount = 0
    voucher_id = None

    if order_in.voucher_code:
        now = datetime.now(UTC)

        voucher_check = (await db.execute(select(Voucher).where(Voucher.code == order_in.voucher_code))).scalar_one_or_none()
        if not voucher_check:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail="Mã Voucher không tồn tại"
            )

        user_used = (
            await db.execute(
                select(VoucherUsage).where(
                    VoucherUsage.voucher_id == voucher_check.id,
                    VoucherUsage.user_id == customer_id
                )
            )
        ).scalar_one_or_none()
        if user_used:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Bạn đã sử dụng mã Voucher này rồi"
            )

        if subtotal < voucher_check.min_order_value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Đơn hàng chưa đạt giá trị tối thiểu ({voucher_check.min_order_value:,}đ) để áp dụng voucher"
            )

        # Tăng used_count có điều kiện used_count < usage_limit
        stmt_voucher = (
            update(Voucher)
            .where(
                Voucher.id == voucher_check.id,
                Voucher.is_active.is_(True),
                Voucher.used_count < Voucher.usage_limit,
                Voucher.valid_from <= now,
                Voucher.valid_to >= now
            )
            .values(used_count=Voucher.used_count + 1)
            .returning(
                Voucher.id, 
                Voucher.discount_type, 
                Voucher.discount_value,
                Voucher.max_discount
            )
            .execution_options(synchronize_session=False)
        )
        v_applied = (await db.execute(stmt_voucher)).first()
        if not v_applied:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Mã Voucher đã hết lượt sử dụng hoặc đã hết hạn"
            )

        voucher_id = v_applied.id
        discount_amount = calculate_voucher_discount(
            subtotal=subtotal,
            discount_type=v_applied.discount_type,
            discount_value=v_applied.discount_value,
            max_discount=v_applied.max_discount or 0
        )

    total_amount = max(0, subtotal - discount_amount) + delivery_fee
    order_code = f"ORD-{uuid4().hex[:8].upper()}"

    # 5. Lưu Order và OrderItems trong Transaction
    order = Order(
        order_code = order_code,
        customer_id = customer_id,
        restaurant_id = restaurant.id,
        status = OrderStatus.SUBMITTED,
        delivery_lat = order_in.delivery_lat,
        delivery_lng = order_in.delivery_lng,
        delivery_address = order_in.delivery_address,
        subtotal = subtotal,
        delivery_fee = delivery_fee,
        surge_multiplier = surge_multiplier,
        discount_amount = discount_amount,
        total_amount = total_amount,
        voucher_id = voucher_id,
        idempotency_key = idempotency_key,
        items = order_items
    )
    db.add(order)
    await db.flush()

    if voucher_id:
        db.add(VoucherUsage(
            voucher_id=voucher_id,
            user_id=customer_id,
            order_id=order.id
        ))

    # 6. Ghi lịch sử trạng thái đầu tiên
    history = OrderStatusHistory(
        order_id = order.id,
        from_status = "NONE",
        to_status = OrderStatus.SUBMITTED.value,
        changed_by_user_id = customer_id,
        reason = "Khách hàng đặt đơn thành công"
    )
    db.add(history)

    await db.commit()
    ORDERS_CREATED_TOTAL.inc()
    return order


async def list_orders_no_n_plus_one(
    db: AsyncSession,
    current_user: User,
    skip: int = 0,
    limit: int = 20
) -> list[Order]:
    """
        Loại bỏ lỗi N+1 Query
    """
    stmt = (
        select(Order)
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc())
        .offset(skip)
        .limit(limit)
    )

    if current_user.role == UserRole.CUSTOMER:
        stmt = stmt.where(Order.customer_id == current_user.id)
    elif current_user.role == UserRole.MERCHANT:
        stmt = stmt.join(Restaurant).where(Restaurant.owner_id == current_user.id)

    result = await db.execute(stmt)
    return list(result.scalars().all())

def calculate_voucher_discount(
        subtotal: int, 
        discount_type: DiscountType, 
        discount_value: int, 
        max_discount: int = 0
) -> int:
    if discount_type == DiscountType.PERCENTAGE:
        raw_discount = int(subtotal * (discount_value / 100))
        if max_discount > 0:
            return min(raw_discount, max_discount)
        return min(raw_discount, subtotal)
    elif discount_type == DiscountType.FIXED:
        return min(discount_value, subtotal)
    return 0

async def get_order_by_idempotency_key(
        db: AsyncSession,
        customer_id: int,
        idempotency_key: str
) -> Order | None:
    """
        Tìm đơn hàng đã tạo trước đó theo idempotency_key của khách
    """
    stmt = (
        select(Order)
        .options(selectinload(Order.items))
        .where(
            Order.customer_id == customer_id,
            Order.idempotency_key == idempotency_key
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()