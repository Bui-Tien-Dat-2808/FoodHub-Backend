import math
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.driver import DriverProfile
from app.models.enums import OrderStatus
from app.models.order import Order

# Múi giờ Việt Nam (UTC+7)
VN_TZ = timezone(timedelta(hours=7))


def calculate_voucher_discount(
    subtotal: int,
    discount_type: str,
    discount_value: int,
    max_discount: int = 0,
) -> int:
    """
    Tính tiền giảm giá từ voucher:
    - PERCENTAGE: Giảm theo %, có chặn trần max_discount nếu max_discount > 0.
    - FIXED: Giảm cố định, không vượt quá subtotal.
    """
    if discount_type == "PERCENTAGE":
        raw_discount = int(subtotal * (discount_value / 100))
        if max_discount > 0:
            return min(raw_discount, max_discount)
        return min(raw_discount, subtotal)
    elif discount_type == "FIXED":
        return min(discount_value, subtotal)
    return 0


def get_surge_multiplier(
    target_time: datetime | None = None,
    demand_ratio: float = 1.0,
) -> tuple[float, str]:
    """
    Xác định hệ số phụ thu cao điểm (Surge Multiplier):
    1. Theo khung giờ cao điểm (múi giờ VN):
       - Trưa (11h00 - 13h00): x1.25
       - Tối (18h00 - 20h00): x1.35
       - Các khung giờ khác: x1.0
    2. Theo tỷ lệ cung cầu (demand_ratio = active_orders / available_drivers):
       - Nếu demand_ratio >= 2.0 (đơn chờ gấp đôi số tài xế rảnh): +0.2x
    - Hệ số tối đa giới hạn ở mức 2.0x
    """
    if target_time is None:
        target_time = datetime.now(UTC)

    # Chuyển về giờ Việt Nam (UTC+7)
    if target_time.tzinfo is None:
        local_time = target_time.replace(tzinfo=UTC).astimezone(VN_TZ)
    else:
        local_time = target_time.astimezone(VN_TZ)

    hour = local_time.hour
    base_multiplier = 1.0
    reason_parts: list[str] = []

    if 11 <= hour < 13:
        base_multiplier = 1.25
        reason_parts.append("Khung giờ cao điểm trưa (11:00 - 13:00)")
    elif 18 <= hour < 20:
        base_multiplier = 1.35
        reason_parts.append("Khung giờ cao điểm tối (18:00 - 20:00)")
    else:
        reason_parts.append("Giá cước tiêu chuẩn")

    if demand_ratio >= 2.0:
        base_multiplier += 0.2
        reason_parts.append("Nhu cầu đặt đơn tăng cao trong khu vực")

    final_multiplier = min(2.0, round(base_multiplier, 2))
    reason = " + ".join(reason_parts)
    return final_multiplier, reason


def calculate_delivery_fee(
    distance_km: float,
    surge_multiplier: float = 1.0,
    base_fee: int = 15000,
    base_km: float = 2.0,
    rate_per_km: int = 5000,
) -> int:
    """
    Tính phí giao hàng có áp dụng hệ số nhân cao điểm (Surge Pricing):
    - Dưới hoặc bằng base_km (2km đầu): base_fee (15.000đ).
    - Vượt quá base_km: mỗi km tiếp theo cộng rate_per_km (5.000đ/km), làm tròn lên km tiếp theo.
    - Áp dụng hệ số surge_multiplier: int(round(cước_cơ_bản * surge_multiplier)).
    """
    if distance_km <= base_km:
        base_calc = base_fee
    else:
        extra_km = math.ceil(distance_km - base_km)
        base_calc = base_fee + int(extra_km * rate_per_km)

    final_fee = int(round(base_calc * surge_multiplier))
    return final_fee


async def compute_dynamic_surge(
    db: AsyncSession | None = None,
    target_lat: float | None = None,
    target_lng: float | None = None,
    check_time: datetime | None = None,
) -> tuple[float, str]:
    """
    Tính toán Surge Multiplier thực tế kết hợp thời gian và tỷ lệ đơn chờ / tài xế online từ DB.
    """
    demand_ratio = 1.0

    if db is not None:
        try:
            # 1. Đếm số đơn hàng đang chờ phục vụ
            waiting_statuses = [
                OrderStatus.SUBMITTED,
                OrderStatus.MERCHANT_ACCEPTED,
                OrderStatus.PREPARING,
                OrderStatus.READY_FOR_PICKUP,
            ]
            stmt_orders = select(func.count(Order.id)).where(Order.status.in_(waiting_statuses))
            waiting_orders_count = (await db.execute(stmt_orders)).scalar() or 0

            # 2. Đếm số tài xế online và rảnh rỗi
            stmt_drivers = select(func.count(DriverProfile.id)).where(
                DriverProfile.is_online.is_(True),
                DriverProfile.is_busy.is_(False),
            )
            available_drivers_count = (await db.execute(stmt_drivers)).scalar() or 0

            if available_drivers_count > 0:
                demand_ratio = waiting_orders_count / available_drivers_count
            elif waiting_orders_count > 0:
                demand_ratio = 3.0  # Có đơn nhưng không có tài xế nào rảnh
        except Exception:
            demand_ratio = 1.0

    return get_surge_multiplier(target_time=check_time, demand_ratio=demand_ratio)

