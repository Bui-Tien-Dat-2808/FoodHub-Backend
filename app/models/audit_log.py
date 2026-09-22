from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)      # VD: REFUND_ORDER, CANCEL_ORDER
    entity: Mapped[str] = mapped_column(String(50), nullable=False, index=True)      # VD: Order, User
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True) # Dữ liệu trước khi sửa
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # Dữ liệu sau khi sửa
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)