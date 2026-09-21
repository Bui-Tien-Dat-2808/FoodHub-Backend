import logging

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.core.celery_app import celery_app, run_async
from app.core.database import AsyncSessionLocal
from app.models.enums import OrderStatus
from app.models.order import Order, OrderStatusHistory
from app.models.promotion import Voucher
from app.models.restaurant import MenuItem

logger = logging.getLogger(__name__)


async def _cancel_order_if_pending(order_id: int) -> dict:
    async with AsyncSessionLocal() as db:
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items))
        )
        order = (await db.execute(stmt)).scalar_one_or_none()

        if not order:
            return {"order_id": order_id, "status": "not_found"}

        # Chỉ huỷ nếu vẫn còn ở trạng thái SUBMITTED (chưa được quán nhận/thanh toán)
        if order.status != OrderStatus.SUBMITTED:
            return {
                "order_id": order_id,
                "status": "skipped",
                "current_status": order.status.value,
            }

        # Chuyển sang trạng thái CANCELLED
        order.status = OrderStatus.CANCELLED
        history = OrderStatusHistory(
            order_id=order.id,
            from_status=OrderStatus.SUBMITTED.value,
            to_status=OrderStatus.CANCELLED.value,
            reason="Hệ thống tự huỷ đơn do quá 15 phút chưa thanh toán",
        )
        db.add(history)

        # Hoàn tồn kho sau khi tự huỷ (Atomic SQL UPDATE)
        for item in order.items:
            await db.execute(
                update(MenuItem)
                .where(MenuItem.id == item.menu_item_id)
                .values(stock_quantity=MenuItem.stock_quantity + item.quantity)
            )

        # Hoàn lượt dùng voucher nếu có
        if order.voucher_id:
            await db.execute(
                update(Voucher)
                .where(Voucher.id == order.voucher_id, Voucher.used_count > 0)
                .values(used_count=Voucher.used_count - 1)
            )

        await db.commit()
        logger.info(f"Đã tự huỷ đơn #{order_id} và hoàn tồn kho")
        return {"order_id": order_id, "status": "cancelled", "restored": True}


@celery_app.task(name="app.tasks.order_tasks.auto_cancel_unpaid_order")
def auto_cancel_unpaid_order(order_id: int) -> dict:
    """Task trễ (15 phút) tự động hủy đơn chưa thanh toán."""
    return run_async(_cancel_order_if_pending(order_id))

