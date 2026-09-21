from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import DiscountType


class Voucher(Base):
    __tablename__ = "vouchers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    discount_type: Mapped[DiscountType] = mapped_column(SQLEnum(DiscountType), nullable=False)
    discount_value: Mapped[int] = mapped_column(Integer, nullable=False) # Giá trị giảm giá (theo % hoặc số tiền cố định)
    min_order_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0) # Giá trị đơn hàng tối thiểu để áp dụng voucher
    max_discount: Mapped[int] = mapped_column(Integer, nullable=True, default=0) # Giá trị giảm giá tối đa (chỉ áp dụng cho voucher giảm theo %)
    usage_limit: Mapped[int] = mapped_column(Integer, nullable=False) # Tổng số lượt toàn hệ thống
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0) # Số lượt đã sử dụng
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

class VoucherUsage(Base):
    __tablename__ = "voucher_usages"
    __table_args__ = (
        UniqueConstraint("voucher_id", "user_id", name="uq_voucher_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    voucher_id: Mapped[int] = mapped_column(Integer, ForeignKey("vouchers.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("orders.id"), nullable=False)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
