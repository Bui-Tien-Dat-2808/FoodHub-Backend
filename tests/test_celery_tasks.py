from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.enums import OrderStatus, UserRole
from app.models.order import Order, OrderItem
from app.models.promotion import Voucher
from app.models.restaurant import MenuItem, Restaurant
from app.models.user import User
from app.tasks.notification_tasks import send_order_status_notification
from app.tasks.order_tasks import auto_cancel_unpaid_order
from app.tasks.payment_tasks import process_mock_payment_with_retry
from app.tasks.reconciliation_tasks import daily_reconciliation_task
from tests.conftest import TestSessionLocal


def test_notification_task_success():
    """Kiểm tra task gửi thông báo mock."""
    result = send_order_status_notification(
        order_id=1,
        new_status="SUBMITTED",
        recipient="customer@foodhub.com"
    )
    assert result["order_id"] == 1
    assert result["new_status"] == "SUBMITTED"
    assert result["recipient"] == "customer@foodhub.com"
    assert result["delivered"] is True


@pytest.mark.anyio
async def test_auto_cancel_unpaid_order_happy_path(db_session):
    """Kiểm tra auto-cancel hủy đơn SUBMITTED và hoàn trả tồn kho + voucher."""
    async with TestSessionLocal() as session:
        # 1. Tạo User, Restaurant, MenuItem (kho = 10)
        merchant = User(email="m_task@foodhub.com", hashed_password="pw", full_name="Merchant", role=UserRole.MERCHANT)
        customer = User(email="c_task@foodhub.com", hashed_password="pw", full_name="Customer", role=UserRole.CUSTOMER)
        session.add_all([merchant, customer])
        await session.commit()

        rest = Restaurant(name="Quan An Task", address="123 Duong A", latitude=10.0, longitude=106.0, owner_id=merchant.id)
        session.add(rest)
        await session.commit()

        item = MenuItem(restaurant_id=rest.id, name="Com Ga", base_price=40000, stock_quantity=10, is_available=True)
        now = datetime.now(UTC)
        voucher = Voucher(
            code="TASKVOUCHER",
            discount_type="FIXED",
            discount_value=10000,
            min_order_value=30000,
            usage_limit=10,
            used_count=1,
            is_active=True,
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=1),
        )
        session.add_all([item, voucher])
        await session.commit()

        # 2. Tạo đơn hàng 2 phần cơm (kho giảm từ 10 -> 8)
        item.stock_quantity = 8
        order = Order(
            order_code="ORD-TASK-001", customer_id=customer.id, restaurant_id=rest.id,
            status=OrderStatus.SUBMITTED, delivery_lat=10.0, delivery_lng=106.0,
            delivery_address="456 Duong B", subtotal=80000, delivery_fee=15000,
            discount_amount=10000, total_amount=85000, voucher_id=voucher.id
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)

        order_item = OrderItem(
            order_id=order.id,
            menu_item_id=item.id,
            name_snapshot=item.name,
            unit_price=item.base_price,
            quantity=2,
            subtotal=item.base_price * 2,
        )
        session.add(order_item)
        await session.commit()
        order_id = order.id
        item_id = item.id
        voucher_id = voucher.id

    # 3. Chạy task auto-cancel
    res = auto_cancel_unpaid_order(order_id)
    assert res["status"] == "cancelled"
    assert res["restored"] is True

    # 4. Kiểm tra Database: đơn đã CANCELLED, kho hoàn về 10, voucher hoàn về 0
    async with TestSessionLocal() as session:
        cancelled_order = (await session.execute(select(Order).where(Order.id == order_id))).scalar_one()
        assert cancelled_order.status == OrderStatus.CANCELLED

        restored_item = (await session.execute(select(MenuItem).where(MenuItem.id == item_id))).scalar_one()
        assert restored_item.stock_quantity == 10

        restored_voucher = (await session.execute(select(Voucher).where(Voucher.id == voucher_id))).scalar_one()
        assert restored_voucher.used_count == 0


@pytest.mark.anyio
async def test_auto_cancel_unpaid_order_idempotent_skip(db_session):
    """Kiểm tra tính Idempotent: Đơn đã MERCHANT_ACCEPTED thì không được tự hủy."""
    async with TestSessionLocal() as session:
        merchant = User(email="m_task2@foodhub.com", hashed_password="pw", full_name="Merchant 2", role=UserRole.MERCHANT)
        customer = User(email="c_task2@foodhub.com", hashed_password="pw", full_name="Customer 2", role=UserRole.CUSTOMER)
        session.add_all([merchant, customer])
        await session.commit()

        rest = Restaurant(name="Quan An Task 2", address="123 Duong A", latitude=10.0, longitude=106.0, owner_id=merchant.id)
        session.add(rest)
        await session.commit()

        order = Order(
            order_code="ORD-TASK-002", customer_id=customer.id, restaurant_id=rest.id,
            status=OrderStatus.MERCHANT_ACCEPTED, # Quán đã nhận đơn!
            delivery_lat=10.0, delivery_lng=106.0, delivery_address="456 Duong B",
            subtotal=50000, delivery_fee=15000, total_amount=65000
        )
        session.add(order)
        await session.commit()
        order_id = order.id

    # Chạy task auto-cancel
    res = auto_cancel_unpaid_order(order_id)
    assert res["status"] == "skipped"

    # Kiểm tra trạng thái đơn vẫn giữ nguyên
    async with TestSessionLocal() as session:
        db_order = (await session.execute(select(Order).where(Order.id == order_id))).scalar_one()
        assert db_order.status == OrderStatus.MERCHANT_ACCEPTED


def test_payment_retry_mechanism():
    """Kiểm tra cơ chế retry của cổng thanh toán khi gặp lỗi mạng tạm thời."""
    # Giả lập fail 2 lần đầu và thành công ở lần thứ 3 qua Celery runner .apply()
    res = process_mock_payment_with_retry.apply(
        args=[99, 150000], kwargs={"simulate_failure_count": 2}
    )
    result = res.result
    assert result["status"] == "SUCCESS"
    assert result["amount"] == 150000
    assert result["retries_count"] == 2


@pytest.mark.anyio
async def test_daily_reconciliation_task(db_session):
    """Kiểm tra task Celery Beat đối soát tính chuẩn xác doanh thu các đơn DELIVERED."""
    async with TestSessionLocal() as session:
        merchant = User(email="m_rec@foodhub.com", hashed_password="pw", full_name="Merchant Rec", role=UserRole.MERCHANT)
        customer = User(email="c_rec@foodhub.com", hashed_password="pw", full_name="Customer Rec", role=UserRole.CUSTOMER)
        session.add_all([merchant, customer])
        await session.commit()

        rest = Restaurant(name="Quan An Rec", address="123 Duong A", latitude=10.0, longitude=106.0, owner_id=merchant.id)
        session.add(rest)
        await session.commit()

        # Đơn 1: DELIVERED (subtotal=100k, fee=20k, total=120k)
        o1 = Order(
            order_code="ORD-REC-001", customer_id=customer.id, restaurant_id=rest.id,
            status=OrderStatus.DELIVERED, delivery_lat=10.0, delivery_lng=106.0,
            delivery_address="456 Duong B", subtotal=100000, delivery_fee=20000, total_amount=120000
        )
        # Đơn 2: DELIVERED (subtotal=200k, fee=30k, total=230k)
        o2 = Order(
            order_code="ORD-REC-002", customer_id=customer.id, restaurant_id=rest.id,
            status=OrderStatus.DELIVERED, delivery_lat=10.0, delivery_lng=106.0,
            delivery_address="456 Duong B", subtotal=200000, delivery_fee=30000, total_amount=230000
        )
        # Đơn 3: CANCELLED (Không được tính vào đối soát)
        o3 = Order(
            order_code="ORD-REC-003", customer_id=customer.id, restaurant_id=rest.id,
            status=OrderStatus.CANCELLED, delivery_lat=10.0, delivery_lng=106.0,
            delivery_address="456 Duong B", subtotal=50000, delivery_fee=15000, total_amount=65000
        )
        session.add_all([o1, o2, o3])
        await session.commit()

    report = daily_reconciliation_task()
    assert report["total_orders"] == 2
    assert report["total_revenue"] == 350000
    assert report["total_subtotal"] == 300000
    assert report["total_delivery_fee"] == 50000