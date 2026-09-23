"""Live-Ops Realtime Dashboard Service for FoodHub Backend.

Provides real-time system-wide operational metrics:
- Active orders count & status distribution
- Total revenue from delivered orders
- Driver availability & activity (online vs busy)
- Realtime WebSocket connections count
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.driver import DriverProfile
from app.models.enums import OrderStatus
from app.models.order import Order
from app.services.websocket_manager import ws_manager

TERMINAL_STATUSES = [
    OrderStatus.DELIVERED,
    OrderStatus.CANCELLED,
    OrderStatus.FAILED_DELIVERY,
]


async def get_live_ops_snapshot(db: AsyncSession) -> dict[str, Any]:
    """Thu thập toàn bộ metrics hoạt động thời gian thực của hệ thống."""
    # 1. Thống kê đơn hàng đang hoạt động (Active Orders)
    active_stmt = (
        select(Order.status, func.count(Order.id))
        .where(Order.status.not_in(TERMINAL_STATUSES))
        .group_by(Order.status)
    )
    active_rows = (await db.execute(active_stmt)).all()
    active_orders_by_status = {
        row[0].value if hasattr(row[0], "value") else str(row[0]): row[1]
        for row in active_rows
    }
    active_orders_count = sum(active_orders_by_status.values())

    # 2. Tổng doanh thu từ các đơn hoàn thành thành công (DELIVERED)
    rev_stmt = select(func.coalesce(func.sum(Order.total_amount), 0)).where(
        Order.status == OrderStatus.DELIVERED
    )
    total_revenue = float((await db.execute(rev_stmt)).scalar() or 0.0)

    # 3. Tổng số lượng đơn hàng tích luỹ
    total_orders_stmt = select(func.count(Order.id))
    total_orders_count = int((await db.execute(total_orders_stmt)).scalar() or 0)

    # 4. Thống kê tài xế (Online & Busy)
    online_stmt = select(func.count(DriverProfile.id)).where(DriverProfile.is_online.is_(True))
    online_drivers_count = int((await db.execute(online_stmt)).scalar() or 0)

    busy_stmt = select(func.count(DriverProfile.id)).where(
        DriverProfile.is_online.is_(True),
        DriverProfile.is_busy.is_(True),
    )
    busy_drivers_count = int((await db.execute(busy_stmt)).scalar() or 0)

    # 5. Số kết nối WebSocket đang hoạt động
    ws_connections = ws_manager.get_total_connections_count()

    return {
        "active_orders_count": active_orders_count,
        "active_orders_by_status": active_orders_by_status,
        "total_delivered_revenue": total_revenue,
        "total_orders_count": total_orders_count,
        "online_drivers_count": online_drivers_count,
        "busy_drivers_count": busy_drivers_count,
        "active_websocket_connections": ws_connections,
    }
