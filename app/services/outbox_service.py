import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox import OutboxEvent
from app.services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)


def record_outbox_event(
    db: AsyncSession,
    aggregate_type: str,
    aggregate_id: str | int,
    event_type: str,
    payload: dict[str, Any],
) -> OutboxEvent:
    """
    Ghi nhận một Outbox Event vào DB session hiện tại:
    - KHÔNG tự commit mà nằm cùng transaction nguyên tử với nghiệp vụ chính (ACID).
    - Ngăn chặn hoàn toàn hiện tượng Dual-Write Failure.
    """
    event = OutboxEvent(
        event_id=f"EVT-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}",
        aggregate_type=str(aggregate_type).upper(),
        aggregate_id=str(aggregate_id),
        event_type=str(event_type).upper(),
        payload_json=json.dumps(payload, default=str),
        status="PENDING",
        retry_count=0,
        max_retries=3,
        created_at=datetime.now(UTC),
    )
    db.add(event)
    return event


async def relay_outbox_events(
    db: AsyncSession,
    redis: aioredis.Redis,
    batch_size: int = 50,
) -> dict[str, Any]:
    """
    Relay Worker: Quét các sự kiện PENDING trong outbox_events và dispatch sang Message Broker / WebSocket:
    - Sử dụng khóa bi quan with_for_update() chống tranh chấp đa worker.
    - Đảm bảo tính chất At-Least-Once Delivery.
    - Cập nhật PROCESSED khi thành công hoặc FAILED khi vượt quá max_retries.
    """
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.status == "PENDING")
        .order_by(OutboxEvent.created_at.asc())
        .limit(batch_size)
        .with_for_update()
    )
    events = (await db.execute(stmt)).scalars().all()

    processed_count = 0
    failed_count = 0

    for ev in events:
        try:
            payload = json.loads(ev.payload_json)

            # 1. Dispatch theo loại sự kiện
            if ev.aggregate_type == "ORDER":
                channel = f"orders:{ev.aggregate_id}"
                await ws_manager.publish(
                    redis=redis,
                    channel=channel,
                    message={
                        "event": ev.event_type,
                        "order_id": int(ev.aggregate_id),
                        "payload": payload,
                    },
                )
                await ws_manager.broadcast_admin_live_ops(
                    redis=redis,
                    message={
                        "event": ev.event_type,
                        "order_id": int(ev.aggregate_id),
                        "payload": payload,
                    },
                )
            else:
                await ws_manager.broadcast_admin_live_ops(
                    redis=redis,
                    message={
                        "event": ev.event_type,
                        "aggregate_type": ev.aggregate_type,
                        "aggregate_id": ev.aggregate_id,
                        "payload": payload,
                    },
                )

            ev.status = "PROCESSED"
            ev.processed_at = datetime.now(UTC)
            processed_count += 1
            logger.info(f"Outbox Relay: Đã dispatch thành công sự kiện #{ev.event_id} ({ev.event_type})")
        except Exception as exc:
            ev.retry_count += 1
            ev.last_error = str(exc)
            logger.warning(f"Outbox Relay: Lỗi dispatch #{ev.event_id} (Lần thử {ev.retry_count}/{ev.max_retries}): {exc}")

            if ev.retry_count >= ev.max_retries:
                ev.status = "FAILED"
                failed_count += 1

    await db.commit()

    return {
        "status": "success",
        "processed_count": processed_count,
        "failed_count": failed_count,
        "total_scanned": len(events),
        "message": f"Đã quét {len(events)} sự kiện: {processed_count} thành công, {failed_count} thất bại",
    }


async def list_outbox_events(
    db: AsyncSession,
    status_filter: str | None = None,
    limit: int = 50,
    skip: int = 0,
) -> list[OutboxEvent]:
    """Admin tra cứu danh sách các sự kiện trong bảng outbox."""
    stmt = select(OutboxEvent).order_by(desc(OutboxEvent.created_at))
    if status_filter:
        stmt = stmt.where(OutboxEvent.status == status_filter.upper())
    stmt = stmt.offset(skip).limit(limit)
    return (await db.execute(stmt)).scalars().all()
