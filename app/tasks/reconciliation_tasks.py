import logging

from sqlalchemy import func, select

from app.core.celery_app import celery_app, run_async
from app.core.database import AsyncSessionLocal
from app.models.enums import OrderStatus
from app.models.order import Order

logger = logging.getLogger(__name__)

async def calculate_daily_reconciliation() -> dict:
    async with AsyncSessionLocal() as db:
        stmt = (
            select(
                func.count(Order.id).label("total_orders"),
                func.coalesce(func.sum(Order.total_amount), 0).label("total_revenue"),
                func.coalesce(func.sum(Order.subtotal), 0).label("total_subtotal"),
                func.coalesce(func.sum(Order.delivery_fee), 0).label("total_delivery_fee"),
            )
            .where(Order.status == OrderStatus.DELIVERED)
        )
        row = (await db.execute(stmt)).one()

        report = {
            "total_orders": row.total_orders,
            "total_revenue": int(row.total_revenue),
            "total_subtotal": int(row.total_subtotal),
            "total_delivery_fee": int(row.total_delivery_fee),
        }

        logger.info(
            f"Báo cáo ngày:"
            f"{report['total_orders']} đơn | Doanh thu: {report['total_revenue']:,}đ | "
            f"Số tiền: {report['total_subtotal']:,}đ | Phí ship: {report['total_delivery_fee']:,}đ"
        )
        return report

@celery_app.task(name="app.tasks.reconciliation_tasks.daily_reconciliation_task")
def daily_reconciliation_task() -> dict:
    return run_async(calculate_daily_reconciliation())