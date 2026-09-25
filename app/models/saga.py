from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SagaInstance(Base):
    """
    Bảng lưu vết phiên thực thi Saga Orchestration.
    Theo dõi tiến độ đa bước, ghi nhận compensation log và phục hồi tự động khi có sự cố.
    """
    __tablename__ = "saga_instances"
    __table_args__ = (
        Index("ix_saga_instances_status_created_at", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    saga_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    saga_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # ORDER_CHECKOUT, ORDER_REFUND
    order_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="STARTED", nullable=False, index=True)  # STARTED, PENDING, COMPENSATING, COMPLETED, FAILED
    current_step: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    compensation_log_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
