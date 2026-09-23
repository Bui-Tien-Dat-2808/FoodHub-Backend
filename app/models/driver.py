from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import BatchStatus, DriverAssignmentStatus, WaypointType

if TYPE_CHECKING:
    from app.models.order import Order
    from app.models.restaurant import Restaurant


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


class DeliveryBatch(Base):
    __tablename__ = "delivery_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    restaurant_id: Mapped[int] = mapped_column(Integer, ForeignKey("restaurants.id"), nullable=False, index=True)
    driver_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("driver_profiles.id"), nullable=True, index=True)
    status: Mapped[BatchStatus] = mapped_column(
        SQLEnum(BatchStatus), default=BatchStatus.PENDING, nullable=False, index=True
    )
    total_distance_km: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    driver: Mapped["DriverProfile | None"] = relationship("DriverProfile")
    restaurant: Mapped["Restaurant"] = relationship("Restaurant")
    assignments: Mapped[list["DriverAssignment"]] = relationship("DriverAssignment", back_populates="batch")
    waypoints: Mapped[list["BatchWaypoint"]] = relationship(
        "BatchWaypoint", back_populates="batch", cascade="all, delete-orphan", order_by="BatchWaypoint.sequence"
    )


class BatchWaypoint(Base):
    __tablename__ = "batch_waypoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(Integer, ForeignKey("delivery_batches.id"), nullable=False, index=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    waypoint_type: Mapped[WaypointType] = mapped_column(SQLEnum(WaypointType), nullable=False)
    target_lat: Mapped[float] = mapped_column(Float, nullable=False)
    target_lng: Mapped[float] = mapped_column(Float, nullable=False)
    target_address: Mapped[str] = mapped_column(String(255), nullable=False)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    batch: Mapped["DeliveryBatch"] = relationship("DeliveryBatch", back_populates="waypoints")
    order: Mapped["Order"] = relationship("Order")


class DriverAssignment(Base):
    __tablename__ = "delivery_assignments"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("orders.id"), unique=True, nullable=False, index=True)
    driver_id: Mapped[int] = mapped_column(Integer, ForeignKey("driver_profiles.id"), nullable=False, index=True)
    batch_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("delivery_batches.id"), nullable=True, index=True)
    status: Mapped[DriverAssignmentStatus] = mapped_column(
        SQLEnum(DriverAssignmentStatus), default=DriverAssignmentStatus.ACCEPTED, nullable=False
    )
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    batch: Mapped["DeliveryBatch | None"] = relationship("DeliveryBatch", back_populates="assignments")
