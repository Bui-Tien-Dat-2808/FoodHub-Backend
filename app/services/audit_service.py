from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


async def log_audit(
    db: AsyncSession,
    actor_id: int | None,
    action: str,
    entity: str,
    entity_id: int,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    ip_address: str | None = None
) -> AuditLog:
    """Ghi lại Audit Trail cho các thao tác nhạy cảm"""
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        before_state=before_state,
        after_state=after_state,
        ip_address=ip_address
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry