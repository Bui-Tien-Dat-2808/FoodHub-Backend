import logging
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import LedgerAccountType, LedgerEntryType, OrderStatus
from app.models.ledger import LedgerEntry
from app.models.order import Order
from app.models.restaurant import Restaurant

logger = logging.getLogger(__name__)

# Mức chiết khấu hoa hồng mặc định của sàn FoodHub (15%)
DEFAULT_COMMISSION_RATE = 0.15


async def record_order_settlement(
    db: AsyncSession,
    order: Order,
    commission_rate: float = DEFAULT_COMMISSION_RATE,
) -> list[LedgerEntry]:
    """
    Hạch toán sổ cái ghi kép (Double-Entry Ledger) khi đơn hàng giao thành công (DELIVERED):
    - DEBIT CUSTOMER: Tổng số tiền khách trả (order.total_amount)
    - CREDIT RESTAURANT: Tiền món ăn sau khi trừ hoa hồng sàn (subtotal - commission)
    - CREDIT PLATFORM: Tiền hoa hồng sàn + Phí giao hàng (commission + delivery_fee - discount)
    Đảm bảo: Tổng DEBIT == Tổng CREDIT (Nguyên tắc bất biến kế toán).
    """
    # 1. Kiểm tra xem đơn hàng đã từng được hạch toán settlement chưa (idempotent)
    stmt_check = select(LedgerEntry).where(
        LedgerEntry.order_id == order.id,
        LedgerEntry.account_type == LedgerAccountType.CUSTOMER,
        LedgerEntry.entry_type == LedgerEntryType.DEBIT,
    )
    existing = (await db.execute(stmt_check)).scalars().first()
    if existing:
        stmt_all = select(LedgerEntry).where(LedgerEntry.order_id == order.id)
        return list((await db.execute(stmt_all)).scalars().all())

    # 2. Tính toán các phân bổ dòng tiền
    subtotal = order.subtotal
    commission_amount = int(round(subtotal * commission_rate))
    restaurant_net_payout = subtotal - commission_amount
    platform_revenue = commission_amount + order.delivery_fee - order.discount_amount
    total_paid = order.total_amount

    # 3. Kiểm tra tính cân đối kép (Debit == Credit)
    debit_total = total_paid
    credit_total = restaurant_net_payout + platform_revenue
    if debit_total != credit_total:
        logger.error(
            "Lệch cán cân kế toán: Debit (%s) != Credit (%s) cho đơn #%s",
            debit_total,
            credit_total,
            order.order_code,
        )
        raise ValueError(
            f"Lỗi cân đối sổ cái: Tổng Nợ ({debit_total}) khác Tổng Có ({credit_total})"
        )

    # 4. Tạo các bút toán
    entries = [
        LedgerEntry(
            order_id=order.id,
            account_type=LedgerAccountType.CUSTOMER,
            entry_type=LedgerEntryType.DEBIT,
            amount=total_paid,
            description=f"Khách hàng thanh toán đơn hàng #{order.order_code}",
        ),
        LedgerEntry(
            order_id=order.id,
            account_type=LedgerAccountType.RESTAURANT,
            entry_type=LedgerEntryType.CREDIT,
            amount=restaurant_net_payout,
            description=f"Doanh thu quán đơn #{order.order_code} (sau trừ {int(commission_rate * 100)}% hoa hồng)",
        ),
        LedgerEntry(
            order_id=order.id,
            account_type=LedgerAccountType.PLATFORM,
            entry_type=LedgerEntryType.CREDIT,
            amount=platform_revenue,
            description=f"Doanh thu hoa hồng và phí ship sàn đơn #{order.order_code}",
        ),
    ]

    db.add_all(entries)
    await db.flush()
    return entries


async def record_order_refund(
    db: AsyncSession,
    order: Order,
    refund_amount: int,
    reason: str,
) -> list[LedgerEntry]:
    """
    Hạch toán sổ cái ghi kép khi hoàn tiền đơn hàng (Refund):
    - DEBIT PLATFORM: Sàn xuất quỹ hoàn tiền
    - CREDIT CUSTOMER: Ví khách hàng nhận tiền hoàn
    Đảm bảo: Tổng DEBIT == Tổng CREDIT.
    """
    if refund_amount <= 0:
        raise ValueError("Số tiền hoàn phải lớn hơn 0")

    entries = [
        LedgerEntry(
            order_id=order.id,
            account_type=LedgerAccountType.PLATFORM,
            entry_type=LedgerEntryType.DEBIT,
            amount=refund_amount,
            description=f"Nền tảng xuất quỹ hoàn tiền đơn #{order.order_code}: {reason}",
        ),
        LedgerEntry(
            order_id=order.id,
            account_type=LedgerAccountType.CUSTOMER,
            entry_type=LedgerEntryType.CREDIT,
            amount=refund_amount,
            description=f"Ví khách hàng nhận hoàn tiền đơn #{order.order_code}: {reason}",
        ),
    ]

    db.add_all(entries)
    await db.flush()
    return entries


async def get_order_ledger_entries(
    db: AsyncSession,
    order_id: int,
) -> list[LedgerEntry]:
    """Lấy toàn bộ các bút toán sổ cái của một đơn hàng."""
    stmt = (
        select(LedgerEntry)
        .where(LedgerEntry.order_id == order_id)
        .order_by(LedgerEntry.created_at.asc(), LedgerEntry.id.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_restaurant_reconciliation(
    db: AsyncSession,
    restaurant_id: int,
) -> dict[str, Any]:
    """
    Tổng hợp sao kê đối soát doanh thu công nợ của nhà hàng:
    - Tổng đơn hoàn thành đã thanh toán
    - Tổng doanh số món ăn Gross
    - Tổng phí hoa hồng sàn chiết khấu
    - Tổng số tiền thực nhận Net Payout
    - Bảng chi tiết từng đơn hàng
    """
    stmt_rest = select(Restaurant).where(Restaurant.id == restaurant_id)
    restaurant = (await db.execute(stmt_rest)).scalar_one_or_none()
    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy nhà hàng",
        )

    # Lấy các đơn DELIVERED của quán kèm ledger_entries
    stmt_orders = (
        select(Order)
        .options(selectinload(Order.ledger_entries))
        .where(
            Order.restaurant_id == restaurant_id,
            Order.status == OrderStatus.DELIVERED,
        )
        .order_by(desc(Order.created_at))
    )
    orders = (await db.execute(stmt_orders)).scalars().all()

    items = []
    total_gross_food = 0
    total_commission_fee = 0
    total_net_payout = 0

    for order in orders:
        # Tìm bút toán CREDIT RESTAURANT trong ledger_entries
        rest_entry = next(
            (
                e
                for e in order.ledger_entries
                if e.account_type == LedgerAccountType.RESTAURANT
                and e.entry_type == LedgerEntryType.CREDIT
            ),
            None,
        )

        subtotal = order.subtotal
        if rest_entry:
            net_payout = rest_entry.amount
            commission_amount = subtotal - net_payout
            settled_at = rest_entry.created_at
        else:
            # Fallback nếu chưa settle sổ cái
            commission_amount = int(round(subtotal * DEFAULT_COMMISSION_RATE))
            net_payout = subtotal - commission_amount
            settled_at = order.created_at

        total_gross_food += subtotal
        total_commission_fee += commission_amount
        total_net_payout += net_payout

        items.append({
            "order_id": order.id,
            "order_code": order.order_code,
            "subtotal": subtotal,
            "commission_rate": DEFAULT_COMMISSION_RATE,
            "commission_amount": commission_amount,
            "net_payout": net_payout,
            "settled_at": settled_at,
        })

    return {
        "restaurant_id": restaurant.id,
        "restaurant_name": restaurant.name,
        "total_settled_orders": len(items),
        "total_gross_food": total_gross_food,
        "total_commission_fee": total_commission_fee,
        "total_net_payout": total_net_payout,
        "items": items,
    }

