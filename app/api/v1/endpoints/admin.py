from datetime import UTC, datetime
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_roles
from app.core.circuit_breaker import circuit_registry
from app.core.database import get_db
from app.core.redis import get_redis
from app.models.audit_log import AuditLog
from app.models.enums import OrderStatus, UserRole
from app.models.order import Order
from app.models.user import User
from app.schemas.admin import AuditLogResponse, RefundRequest, RefundResponse
from app.schemas.ledger import LedgerEntryResponse
from app.schemas.order import OrderResponse
from app.schemas.outbox import OutboxEventResponse, OutboxRelayResponse
from app.schemas.saga import SagaInstanceResponse
from app.services.audit_service import log_audit
from app.services.ledger_service import get_order_ledger_entries, record_order_refund
from app.services.outbox_service import list_outbox_events, relay_outbox_events
from app.services.saga_service import get_saga_detail

router = APIRouter()

@router.get("/orders", response_model=list[OrderResponse])
async def list_all_orders(
    current_user: Annotated[User, Depends(require_roles([UserRole.ADMIN]))],
    db: Annotated[AsyncSession, Depends(get_db)],
    order_status: OrderStatus | None = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100)
):
    """Admin xem list toàn bộ đơn hàng trên hệ thống"""
    stmt = select(Order).options(selectinload(Order.items)).order_by(desc(Order.created_at))
    if order_status:
        stmt = stmt.where(Order.status == order_status)
    stmt = stmt.offset(skip).limit(limit)
    orders = (await db.execute(stmt)).scalars().all()
    return orders

@router.post("/orders/{order_id}/refund", response_model=RefundResponse)
async def refund_order(
    order_id: int,
    data: RefundRequest,
    request: Request,
    current_user: Annotated[User, Depends(require_roles([UserRole.ADMIN]))],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    """
        Admin hoàn tiền cho đơn hàng bị huỷ/giao thất bại
        1. Kiểm tra đơn hàng tồn tại
        2. Chỉ cho phép hoàn tiền với những đơn hàng CANCELLED hoặc FAILED_DELIVERY
        3. Ghi AuditLog
    """

    stmt = select(Order).where(Order.id == order_id)
    order = (await db.execute(stmt)).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy đơn hàng")

    # Chỉ cho phép hoàn tiền nếu đơn bị huỷ hoặc giao thất bại
    if order.status not in (OrderStatus.CANCELED, OrderStatus.FAILED_DELIVERY):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Chỉ hoàn tiền cho đơn đã HUỶ hoặc GIAO THẤT BẠI"
        )

    # Xác định số tiền hoàn
    refund_amount = data.amount if data.amount is not None else order.total_amount
    if refund_amount > order.total_amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Số tiền hoàn không thể lớn hơn số tiền {order.total_amount}đ của đơn hàng"
        )

    # Ghi nhận Audit Trail
    ip_addr = request.client.host if request.client else None
    await log_audit(
        db=db,
        actor_id=current_user.id,
        action="REFUND_ORDER",
        entity="Order",
        entity_id=order.id,
        before_state={"status": order.status.value, "total_amount": order.total_amount},
        after_state={"refund_amount": refund_amount, "reason": data.reason},
        ip_address=ip_addr
    )

    # Ghi nhận bút toán sổ cái ghi kép (Double-Entry Ledger)
    await record_order_refund(
        db=db,
        order=order,
        refund_amount=refund_amount,
        reason=data.reason,
    )
    await db.commit()

    return RefundResponse(
        order_id=order.id,
        refund_amount=refund_amount,
        reason=data.reason,
        status="REFUNDED",
        refunded_at=datetime.now(UTC),
    )


@router.get("/orders/{order_id}/ledger", response_model=list[LedgerEntryResponse])
async def get_order_ledger(
    order_id: int,
    current_user: Annotated[User, Depends(require_roles([UserRole.ADMIN]))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Admin xem toàn bộ các bút toán sổ cái ghi kép của một đơn hàng."""
    entries = await get_order_ledger_entries(db=db, order_id=order_id)
    return entries


@router.get("/audit-logs", response_model=list[AuditLogResponse])
async def list_audit_logs(
    current_user: Annotated[User, Depends(require_roles([UserRole.ADMIN]))],
    db: Annotated[AsyncSession, Depends(get_db)],
    entity: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """Admin xem danh sách nhật ký kiểm toán hệ thống."""
    stmt = select(AuditLog).order_by(desc(AuditLog.created_at))
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    stmt = stmt.offset(skip).limit(limit)
    logs = (await db.execute(stmt)).scalars().all()
    return logs


# ==========================================
# RESILIENCE & CIRCUIT BREAKER MONITORING
# ==========================================

@router.get("/resilience/circuit-breakers")
async def list_circuit_breakers(
    current_user: Annotated[User, Depends(require_roles([UserRole.ADMIN]))],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
):
    """Admin theo dõi trạng thái sức khỏe và độ trễ của toàn bộ Circuit Breakers."""
    breakers = circuit_registry.get_all()
    snapshots = []
    for b in breakers:
        snap = await b.get_snapshot(redis=redis)
        snapshots.append(snap)
    return snapshots


@router.post("/resilience/circuit-breakers/{name}/reset")
async def reset_circuit_breaker(
    name: str,
    current_user: Annotated[User, Depends(require_roles([UserRole.ADMIN]))],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
):
    """Admin chủ động can thiệp đóng lại mạch (reset về CLOSED) cho dịch vụ ngoại vi."""
    breaker = circuit_registry.get(name)
    if not breaker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy Circuit Breaker '{name}'",
        )
    await breaker.reset(redis=redis)
    return {
        "status": "success",
        "message": f"Circuit Breaker '{name}' đã được reset thành công về trạng thái CLOSED",
        "snapshot": await breaker.get_snapshot(redis=redis),
    }


# ==========================================
# SAGA ORCHESTRATION & TRANSACTIONAL OUTBOX (HƯỚNG 4)
# ==========================================

@router.get("/sagas/{saga_id}", response_model=SagaInstanceResponse)
async def get_saga_instance(
    saga_id: str,
    current_user: Annotated[User, Depends(require_roles([UserRole.ADMIN]))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Admin tra cứu chi tiết tiến trình, trạng thái và nhật ký bù trừ của một Saga."""
    saga = await get_saga_detail(db=db, saga_id=saga_id)
    if not saga:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy SagaInstance với mã '{saga_id}'",
        )
    return saga


@router.post("/outbox/relay", response_model=OutboxRelayResponse)
async def trigger_outbox_relay(
    current_user: Annotated[User, Depends(require_roles([UserRole.ADMIN]))],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    batch_size: int = Query(50, ge=1, le=200, description="Số lượng sự kiện xử lý tối đa"),
):
    """Kích hoạt thủ công hoặc định kỳ Relay Worker quét và phát tán các Outbox Events đang PENDING."""
    result = await relay_outbox_events(db=db, redis=redis, batch_size=batch_size)
    return result


@router.get("/outbox/events", response_model=list[OutboxEventResponse])
async def get_outbox_events(
    current_user: Annotated[User, Depends(require_roles([UserRole.ADMIN]))],
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: str | None = Query(None, alias="status", description="Lọc theo trạng thái: PENDING, PROCESSED, FAILED"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """Admin tra cứu danh sách các sự kiện trong bảng outbox_events."""
    events = await list_outbox_events(db=db, status_filter=status_filter, limit=limit, skip=skip)
    return events