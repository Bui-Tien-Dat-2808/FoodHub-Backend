from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import DriverAssignmentStatus


class DriverProfile(Base):
    __tablename__ = "driver_profiles"
    __table_args__ = (
        Index("ix_driver_profiles_spatial", "is_online", "is_busy", "current_lat", "current_lng"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    license_plate: Mapped[str] = mapped_column(String(20), nullable=False)
    current_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)
    is_busy: Mapped[bool] = mapped_column(Boolean, default=False)
    rating: Mapped[float] = mapped_column(Float, default=5.0)

class DriverAssignment(Base):
    __tablename__ = "delivery_assignments"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("orders.id"), unique=True, nullable=False, index=True)
    driver_id: Mapped[int] = mapped_column(Integer, ForeignKey("driver_profiles.id"), nullable=False, index=True)
    status: Mapped[DriverAssignmentStatus] = mapped_column(
        SQLEnum(DriverAssignmentStatus), default=DriverAssignmentStatus.ACCEPTED, nullable=False
    )
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
