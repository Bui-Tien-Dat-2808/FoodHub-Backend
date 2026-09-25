import json
import logging
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.circuit_breaker import circuit_registry
from app.models.enums import OrderStatus
from app.models.order import Order, OrderStatusHistory
from app.models.promotion import Voucher, VoucherUsage
from app.models.restaurant import MenuItem
from app.models.saga import SagaInstance
from app.services.outbox_service import record_outbox_event
from app.services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)


class SagaStep(ABC):
    """Lớp trừu tượng cho từng bước trong chuỗi giao dịch phân tán (Saga Step)."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def execute(self, db: AsyncSession, context: dict[str, Any]) -> None:
        """Thực thi thao tác theo chiều thuận."""
        pass

    @abstractmethod
    async def compensate(self, db: AsyncSession, context: dict[str, Any]) -> dict[str, Any]:
        """Thao tác bù trừ (Compensating Transaction) khi xảy ra sự cố ở các bước sau."""
        pass


class ReserveInventoryStep(SagaStep):
    """Bước 1: Giữ chỗ tồn kho món ăn."""

    def __init__(self):
        super().__init__("ReserveInventory")

    async def execute(self, db: AsyncSession, context: dict[str, Any]) -> None:
        order: Order = context["order"]
        reserved_items = []

        for item_order in order.items:
            stmt = select(MenuItem).where(MenuItem.id == item_order.menu_item_id).with_for_update()
            menu_item = (await db.execute(stmt)).scalar_one_or_none()

            if not menu_item or menu_item.stock_quantity < item_order.quantity:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Món ăn '{item_order.name_snapshot}' không còn đủ tồn kho",
                )

            menu_item.stock_quantity -= item_order.quantity
            reserved_items.append({
                "menu_item_id": menu_item.id,
                "name": menu_item.name,
                "quantity": item_order.quantity,
            })

        context["reserved_items"] = reserved_items

    async def compensate(self, db: AsyncSession, context: dict[str, Any]) -> dict[str, Any]:
        reserved_items = context.get("reserved_items", [])
        for item_data in reserved_items:
            stmt = select(MenuItem).where(MenuItem.id == item_data["menu_item_id"]).with_for_update()
            menu_item = (await db.execute(stmt)).scalar_one_or_none()
            if menu_item:
                menu_item.stock_quantity += item_data["quantity"]

        return {
            "step": self.name,
            "action": "RESTORE_INVENTORY",
            "items_count": len(reserved_items),
        }


class ApplyVoucherStep(SagaStep):
    """Bước 2: Áp dụng mã giảm giá Voucher (nếu có)."""

    def __init__(self):
        super().__init__("ApplyVoucher")

    async def execute(self, db: AsyncSession, context: dict[str, Any]) -> None:
        order: Order = context["order"]
        if not order.voucher_id:
            context["voucher_applied"] = False
            return

        stmt = select(Voucher).where(Voucher.id == order.voucher_id).with_for_update()
        voucher = (await db.execute(stmt)).scalar_one_or_none()

        if not voucher or not voucher.is_active or voucher.used_count >= voucher.usage_limit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Mã voucher không hợp lệ hoặc đã hết lượt sử dụng",
            )

        voucher.used_count += 1
        usage = VoucherUsage(
            voucher_id=voucher.id,
            user_id=order.customer_id,
            order_id=order.id,
        )
        db.add(usage)
        await db.flush()
        context["voucher_applied"] = True
        context["voucher_id"] = voucher.id
        context["voucher_usage"] = usage

    async def compensate(self, db: AsyncSession, context: dict[str, Any]) -> dict[str, Any]:
        if not context.get("voucher_applied"):
            return {"step": self.name, "action": "SKIP", "reason": "No voucher applied"}

        voucher_id = context.get("voucher_id")
        stmt_v = select(Voucher).where(Voucher.id == voucher_id).with_for_update()
        voucher = (await db.execute(stmt_v)).scalar_one_or_none()
        if voucher and voucher.used_count > 0:
            voucher.used_count -= 1

        usage = context.get("voucher_usage")
        if usage:
            await db.delete(usage)
        else:
            order: Order = context["order"]
            stmt_u = select(VoucherUsage).where(
                VoucherUsage.voucher_id == voucher_id,
                VoucherUsage.order_id == order.id,
            )
            db_usage = (await db.execute(stmt_u)).scalar_one_or_none()
            if db_usage:
                await db.delete(db_usage)

        await db.flush()
        return {
            "step": self.name,
            "action": "REVERT_VOUCHER_USAGE",
            "voucher_id": voucher_id,
        }


class ProcessPaymentStep(SagaStep):
    """Bước 3: Xử lý thanh toán qua Circuit Breaker."""

    def __init__(self, simulate_failure: bool = False):
        super().__init__("ProcessPayment")
        self.simulate_failure = simulate_failure

    async def execute(self, db: AsyncSession, context: dict[str, Any]) -> None:
        if self.simulate_failure:
            raise RuntimeError("Cổng thanh toán phản hồi lỗi từ chối giao dịch (Simulated Payment Failure)")

        order: Order = context["order"]
        redis: aioredis.Redis = context["redis"]
        breaker = circuit_registry.get("payment_gateway")

        async def _call_gateway(amount: int):
            # Giả lập kết nối cổng thanh toán an toàn
            return {"transaction_id": f"TXN-{uuid4().hex[:10].upper()}", "amount": amount, "status": "PAID"}

        if breaker:
            payment_res = await breaker.execute(_call_gateway, order.total_amount, redis=redis)
        else:
            payment_res = await _call_gateway(order.total_amount)

        context["payment_processed"] = True
        context["payment_txn"] = payment_res.get("transaction_id")

    async def compensate(self, db: AsyncSession, context: dict[str, Any]) -> dict[str, Any]:
        if not context.get("payment_processed"):
            return {"step": self.name, "action": "SKIP", "reason": "Payment was not completed"}

        txn = context.get("payment_txn")
        logger.info(f"Saga Compensate: Hoàn tiền giao dịch #{txn}")
        return {
            "step": self.name,
            "action": "REFUND_PAYMENT",
            "transaction_id": txn,
        }


class ConfirmOrderStep(SagaStep):
    """Bước 4: Chuyển trạng thái đơn sang MERCHANT_ACCEPTED và lưu Transactional Outbox Event."""

    def __init__(self):
        super().__init__("ConfirmOrder")

    async def execute(self, db: AsyncSession, context: dict[str, Any]) -> None:
        order: Order = context["order"]
        old_status = order.status
        order.status = OrderStatus.MERCHANT_ACCEPTED

        history = OrderStatusHistory(
            order_id=order.id,
            from_status=old_status.value,
            to_status=OrderStatus.MERCHANT_ACCEPTED.value,
            changed_by_user_id=order.customer_id,
            reason="Saga Orchestrator: Thanh toán & xác nhận đơn thành công",
        )
        db.add(history)

        # Lưu Outbox Event nguyên tử
        record_outbox_event(
            db=db,
            aggregate_type="ORDER",
            aggregate_id=order.id,
            event_type="ORDER_PAID",
            payload={
                "order_id": order.id,
                "status": OrderStatus.MERCHANT_ACCEPTED.value,
                "total_amount": order.total_amount,
            },
        )
        context["order_confirmed"] = True

    async def compensate(self, db: AsyncSession, context: dict[str, Any]) -> dict[str, Any]:
        order: Order = context["order"]
        order.status = OrderStatus.CANCELLED

        history = OrderStatusHistory(
            order_id=order.id,
            from_status=order.status.value,
            to_status=OrderStatus.CANCELLED.value,
            changed_by_user_id=order.customer_id,
            reason="Saga Orchestrator: Bù trừ hủy đơn do sự cố thanh toán",
        )
        db.add(history)

        record_outbox_event(
            db=db,
            aggregate_type="ORDER",
            aggregate_id=order.id,
            event_type="ORDER_CANCELLED",
            payload={"order_id": order.id, "status": OrderStatus.CANCELLED.value},
        )
        return {
            "step": self.name,
            "action": "CANCEL_ORDER",
            "order_id": order.id,
        }


async def run_order_checkout_saga(
    db: AsyncSession,
    redis: aioredis.Redis,
    order_id: int,
    simulate_payment_failure: bool = False,
) -> dict[str, Any]:
    """
    Saga Orchestrator điều phối giao dịch Checkout:
    1. Giữ tồn kho món ăn (ReserveInventory)
    2. Áp dụng Voucher (ApplyVoucher)
    3. Xử lý thanh toán qua Circuit Breaker (ProcessPayment)
    4. Xác nhận đơn & lưu Transactional Outbox (ConfirmOrder)
    Nếu bất kỳ bước nào thất bại -> Tự động kích hoạt bù trừ ngược chiều (Compensating Transactions).
    """
    stmt = select(Order).options(selectinload(Order.items)).where(Order.id == order_id).with_for_update()
    order = (await db.execute(stmt)).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy đơn hàng")

    if order.status not in (OrderStatus.SUBMITTED, OrderStatus.READY_FOR_PICKUP):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Đơn hàng ở trạng thái '{order.status.value}' không thể kích hoạt checkout saga",
        )

    saga_id = f"SAGA-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}"
    saga_record = SagaInstance(
        saga_id=saga_id,
        saga_type="ORDER_CHECKOUT",
        order_id=order.id,
        status="STARTED",
        current_step="INIT",
        payload_json=json.dumps({"order_id": order.id, "amount": order.total_amount}),
        compensation_log_json="[]",
        created_at=datetime.now(UTC),
    )
    db.add(saga_record)
    await db.flush()

    steps: list[SagaStep] = [
        ReserveInventoryStep(),
        ApplyVoucherStep(),
        ProcessPaymentStep(simulate_failure=simulate_payment_failure),
        ConfirmOrderStep(),
    ]

    context: dict[str, Any] = {
        "order": order,
        "redis": redis,
        "reserved_items": [],
        "voucher_applied": False,
        "payment_processed": False,
        "order_confirmed": False,
    }

    completed_steps: list[SagaStep] = []
    compensation_log: list[dict[str, Any]] = []

    try:
        saga_record.status = "PENDING"
        for step in steps:
            saga_record.current_step = step.name
            logger.info(f"Saga [{saga_id}]: Bắt đầu bước {step.name}")
            await step.execute(db=db, context=context)
            completed_steps.append(step)

        # Hoàn thành tất cả các bước thuận lợi
        saga_record.status = "COMPLETED"
        saga_record.current_step = "DONE"
        await db.commit()

        # Phát sóng WebSocket
        await ws_manager.publish(
            redis=redis,
            channel=f"orders:{order.id}",
            message={
                "event": "ORDER_STATUS_CHANGED",
                "order_id": order.id,
                "status": OrderStatus.MERCHANT_ACCEPTED.value,
                "saga_id": saga_id,
            },
        )

        return {
            "saga_id": saga_id,
            "order_id": order.id,
            "status": "COMPLETED",
            "current_step": "DONE",
            "steps_completed": [s.name for s in completed_steps],
            "is_successful": True,
            "message": "Saga Checkout hoàn tất thành công",
            "compensation_log": [],
        }

    except Exception as exc:
        logger.error(f"Saga [{saga_id}] thất bại tại bước {saga_record.current_step}: {exc}. Bắt đầu bù trừ...")
        saga_record.status = "COMPENSATING"
        saga_record.error_message = str(exc)

        # Kích hoạt Compensating Transactions theo thứ tự LIFO (ngược chiều)
        for completed_step in reversed(completed_steps):
            try:
                comp_result = await completed_step.compensate(db=db, context=context)
                compensation_log.append(comp_result)
                logger.info(f"Saga [{saga_id}]: Đã bù trừ bước {completed_step.name}")
            except Exception as comp_err:
                logger.critical(f"Saga [{saga_id}]: Bù trừ bước {completed_step.name} gặp lỗi: {comp_err}")
                compensation_log.append({"step": completed_step.name, "error": str(comp_err)})

        order.status = OrderStatus.CANCELLED
        saga_record.status = "FAILED"
        saga_record.compensation_log_json = json.dumps(compensation_log)

        # Ghi nhận lịch sử trạng thái đơn hàng
        history = OrderStatusHistory(
            order_id=order.id,
            from_status=OrderStatus.SUBMITTED.value,
            to_status=OrderStatus.CANCELLED.value,
            changed_by_user_id=None,
            reason=f"Saga checkout failed at {saga_record.current_step}: {exc}",
        )
        db.add(history)

        # Ghi nhận Transactional Outbox Event nguyên tử cùng transaction huỷ đơn
        record_outbox_event(
            db=db,
            aggregate_type="ORDER",
            aggregate_id=str(order.id),
            event_type="ORDER_CANCELLED",
            payload={
                "order_id": order.id,
                "saga_id": saga_id,
                "reason": str(exc),
                "status": OrderStatus.CANCELLED.value,
            },
        )
        await db.commit()

        # Phát sóng WebSocket thông báo huỷ đơn
        try:
            await ws_manager.publish(
                redis=redis,
                channel=f"orders:{order.id}",
                message={
                    "event": "ORDER_STATUS_CHANGED",
                    "order_id": order.id,
                    "status": OrderStatus.CANCELLED.value,
                    "saga_id": saga_id,
                    "reason": str(exc),
                },
            )
        except Exception as ws_err:
            logger.warning(f"Saga [{saga_id}]: Không thể phát sóng WebSocket huỷ đơn: {ws_err}")

        return {
            "saga_id": saga_id,
            "order_id": order.id,
            "status": "FAILED",
            "current_step": saga_record.current_step,
            "steps_completed": [s.name for s in completed_steps],
            "is_successful": False,
            "message": f"Saga thất bại: {exc}. Các bước đã được bù trừ an toàn.",
            "compensation_log": compensation_log,
            "error": str(exc),
        }


async def get_saga_detail(db: AsyncSession, saga_id: str) -> SagaInstance | None:
    """Tra cứu chi tiết một SagaInstance theo mã saga_id."""
    stmt = select(SagaInstance).where(SagaInstance.saga_id == saga_id)
    return (await db.execute(stmt)).scalar_one_or_none()
