from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Restaurant(Base):
    __tablename__ = "restaurants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    delivery_radius_km: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    menu_items: Mapped[list["MenuItem"]] = relationship("MenuItem", back_populates="restaurant", cascade="all, delete-orphan")

class MenuItem(Base):
    __tablename__ = "menu_items"
    __table_args__ = (
        CheckConstraint("stock_quantity >= 0", name="check_positive_stock"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    restaurant_id: Mapped[int] = mapped_column(Integer, ForeignKey("restaurants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    base_price: Mapped[int] = mapped_column(Integer, nullable=False) # VND
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=100) # Số lượng món ăn còn lại trong kho
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    restaurant: Mapped["Restaurant"] = relationship("Restaurant", back_populates="menu_items")