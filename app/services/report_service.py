import json
from datetime import UTC, date, datetime, time
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import OrderStatus
from app.models.order import Order, OrderItem


async def get_revenue_report(
    db: AsyncSession,
    redis: aioredis.Redis,
    restaurant_id: int | None,
    start_date: date,
    end_date: date
) -> dict[str, Any]:
    """
        Báo cáo doanh thu với Redis Cache
        1. Check cache Redis
        2. Nếu miss: Chạy SQL Aggregation
        3. Lưu cache trong 600s
    """
    scope_str = str(restaurant_id) if restaurant_id is not None else "all"
    cache_key = f"reports:revenue:{scope_str}:{start_date.isoformat()}:{end_date.isoformat()}"

    # 1. Kiểm tra Cache Hit
    try:
        cached = await redis.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    # Chuyển đổi date -> datetime (UTC)
    start_dt = datetime.combine(start_date, time.min).replace(tzinfo=UTC)
    end_dt = datetime.combine(end_date, time.max).replace(tzinfo=UTC)

    # 2. Truy vấn tổng hợp: Doanh thu, số đơn, phí ship, chiết khấu
    stmt_totals = (
        select(
            func.count(Order.id).label("total_orders"),
            func.coalesce(func.sum(Order.total_amount), 0).label("total_revenue"),
            func.coalesce(func.sum(Order.subtotal), 0).label("total_food_amount"),
            func.coalesce(func.sum(Order.delivery_fee), 0).label("total_delivery_fees"),
            func.coalesce(func.sum(Order.discount_amount), 0).label("total_discount_amount"),
        )
        .where(
            Order.status == OrderStatus.DELIVERED,
            Order.created_at >= start_dt,
            Order.created_at <= end_dt
        )
    )
    if restaurant_id is not None:
        stmt_totals = stmt_totals.where(Order.restaurant_id == restaurant_id)

    totals = (await db.execute(stmt_totals)).one()

    total_orders = totals.total_orders or 0
    total_revenue = float(totals.total_revenue or 0)
    total_food_amount = int(totals.total_food_amount or 0)
    total_delivery_fees = int(totals.total_delivery_fees or 0)
    total_discount_amount = int(totals.total_discount_amount or 0)
    aov = round(total_revenue / total_orders, 2) if total_orders > 0 else 0.0

    # 3. Truy vấn Top 5 món bán chạy nhất
    stmt_top = (
        select(
            OrderItem.menu_item_id,
            OrderItem.name_snapshot,
            func.sum(OrderItem.quantity).label("total_qty"),
            func.sum(OrderItem.subtotal).label("total_rev")
        )
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.status == OrderStatus.DELIVERED,
            Order.created_at >= start_dt,
            Order.created_at <= end_dt
        )
    )
    if restaurant_id is not None:
        stmt_top = stmt_top.where(Order.restaurant_id == restaurant_id)

    stmt_top = (
        stmt_top
        .group_by(OrderItem.menu_item_id, OrderItem.name_snapshot)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(5)
    )
    top_items = (await db.execute(stmt_top)).all()

    top_selling = [
        {
            "menu_item_id": row.menu_item_id,
            "name": row.name_snapshot,
            "total_quantity": int(row.total_qty),
            "total_revenue": float(row.total_rev)
        }
        for row in top_items
    ]

    report_results = {
        "restaurant_id": restaurant_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "total_food_amount": total_food_amount,
        "total_delivery_fees": total_delivery_fees,
        "total_discount_amount": total_discount_amount,
        "average_order_value": aov,
        "top_selling_items": top_selling,
    }

    # 4. Lưu Cache vào Redis với TTL = 600s
    try:
        await redis.set(cache_key, json.dumps(report_results), ex=600)
    except Exception:
        pass

    return report_results

async def invalidate_revenue_report_cache(redis: aioredis.Redis, restaurant_id: int) -> None:
    """Xoá cache báo cáo của quán khi có đơn hoàn thành (DELIVERED)"""
    try:
        pattern_restaurant = f"reports:revenue:{restaurant_id}:*"
        pattern_all = "reports:revenue:all:*"

        for pattern in (pattern_restaurant, pattern_all):
            keys = await redis.keys(pattern)
            if keys:
                await redis.delete(*keys)
    except Exception:
        pass