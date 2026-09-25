"""Tests for Direction 4: Transactional Outbox Pattern & Saga Orchestration.

Validates:
1. Outbox Event atomic creation: generated within the same ACID transaction as order creation.
2. Outbox Relay processing: polls PENDING events, dispatches them, and marks them PROCESSED.
3. Outbox Relay retry & failure: increments retry_count and marks FAILED after max_retries.
4. Order Checkout Saga happy path: reserves inventory, applies voucher, completes payment,
   confirms order (MERCHANT_ACCEPTED), and logs ORDER_PAID outbox event.
5. Order Checkout Saga compensating transactions: on payment failure, automatically rolls back
   reserved inventory, reverts voucher usage, marks order CANCELLED, and sets Saga to FAILED.
6. Admin Saga and Outbox monitoring endpoints.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import OrderStatus
from app.models.order import Order
from app.models.outbox import OutboxEvent
from app.models.promotion import DiscountType, Voucher, VoucherUsage
from app.models.restaurant import MenuItem, Restaurant
from app.models.saga import SagaInstance
from app.models.user import User
from app.services.outbox_service import record_outbox_event, relay_outbox_events


async def _create_test_user(client: AsyncClient, email: str, role: str) -> tuple[str, dict[str, str]]:
    """Helper đăng ký & đăng nhập user."""
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "full_name": f"User {role}", "role": role},
    )
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "password123"},
    )
    token = res.json()["access_token"]
    return token, {"Authorization": f"Bearer {token}"}


@pytest.mark.anyio
async def test_outbox_event_atomic_creation(client: AsyncClient, db_session: AsyncSession):
    """Kiểm tra Outbox Event được tạo nguyên tử cùng lúc với đơn hàng (ACID)."""
    # 1. Tạo Merchant & Quán & Món ăn
    _, m_headers = await _create_test_user(client, "merch_outbox@foodhub.com", "MERCHANT")
    m_user = (await db_session.execute(select(User).where(User.email == "merch_outbox@foodhub.com"))).scalar_one()

    rest = Restaurant(owner_id=m_user.id, name="Quan Outbox", address="10 Nguyen Trai", latitude=10.77, longitude=106.70)
    db_session.add(rest)
    await db_session.commit()

    item = MenuItem(restaurant_id=rest.id, name="Mon Outbox", base_price=50000, stock_quantity=10, is_available=True)
    db_session.add(item)
    await db_session.commit()

    # 2. Tạo Customer & Đặt đơn
    _, c_headers = await _create_test_user(client, "cust_outbox@foodhub.com", "CUSTOMER")
    order_res = await client.post(
        "/api/v1/orders/",
        headers=c_headers,
        json={
            "restaurant_id": rest.id,
            "items": [{"menu_item_id": item.id, "quantity": 2}],
            "delivery_lat": 10.771,
            "delivery_lng": 106.701,
            "delivery_address": "20 Nguyen Trai",
        },
    )
    assert order_res.status_code == 201
    order_id = order_res.json()["id"]

    # 3. Kiểm tra Outbox Event trong DB
    stmt_evt = select(OutboxEvent).where(
        OutboxEvent.aggregate_type == "ORDER",
        OutboxEvent.aggregate_id == str(order_id),
        OutboxEvent.event_type == "ORDER_CREATED",
    )
    event = (await db_session.execute(stmt_evt)).scalar_one_or_none()
    assert event is not None
    assert event.status == "PENDING"
    assert event.retry_count == 0


@pytest.mark.anyio
async def test_outbox_relay_processing(client: AsyncClient, db_session: AsyncSession, fake_redis):
    """Kiểm tra Relay Worker quét sự kiện PENDING và chuyển trạng thái sang PROCESSED."""
    # 1. Tạo event giả lập trong DB
    event = record_outbox_event(
        db=db_session,
        aggregate_type="ORDER",
        aggregate_id="999",
        event_type="ORDER_CREATED",
        payload={"order_id": 999, "total_amount": 100000},
    )
    await db_session.commit()
    await db_session.refresh(event)
    assert event.status == "PENDING"

    # 2. Chạy relay
    result = await relay_outbox_events(db=db_session, redis=fake_redis, batch_size=10)
    assert result["processed_count"] >= 1

    # 3. Kiểm tra event trong DB đã đổi sang PROCESSED
    await db_session.refresh(event)
    assert event.status == "PROCESSED"
    assert event.processed_at is not None


@pytest.mark.anyio
async def test_outbox_relay_retry_and_failure(db_session: AsyncSession, fake_redis, monkeypatch):
    """Kiểm tra cơ chế retry và đánh dấu FAILED khi gặp lỗi liên tiếp."""
    event = record_outbox_event(
        db=db_session,
        aggregate_type="ORDER",
        aggregate_id="888",
        event_type="ORDER_CREATED",
        payload={"order_id": 888},
    )
    event.max_retries = 2
    await db_session.commit()

    # Mock lỗi khi publish WebSocket
    async def mock_fail_publish(*args, **kwargs):
        raise ConnectionError("Broker unreachable")

    from app.services.websocket_manager import ws_manager
    monkeypatch.setattr(ws_manager, "publish", mock_fail_publish)

    # Lần 1: Lỗi -> retry_count = 1, status vẫn PENDING
    await relay_outbox_events(db=db_session, redis=fake_redis, batch_size=10)
    await db_session.refresh(event)
    assert event.retry_count == 1
    assert event.status == "PENDING"

    # Lần 2: Lỗi -> retry_count = 2 >= max_retries -> status FAILED
    await relay_outbox_events(db=db_session, redis=fake_redis, batch_size=10)
    await db_session.refresh(event)
    assert event.retry_count == 2
    assert event.status == "FAILED"


@pytest.mark.anyio
async def test_order_checkout_saga_happy_path(client: AsyncClient, db_session: AsyncSession):
    """Kiểm tra luồng Saga Checkout thành công toàn trình (Kho trừ -> Voucher tăng -> Thanh toán -> Xác nhận đơn)."""
    # 1. Thiết lập Merchant, Quán, Món ăn (stock=10), Voucher
    _, m_headers = await _create_test_user(client, "merch_saga_ok@foodhub.com", "MERCHANT")
    m_user = (await db_session.execute(select(User).where(User.email == "merch_saga_ok@foodhub.com"))).scalar_one()

    rest = Restaurant(owner_id=m_user.id, name="Quan Saga OK", address="1 Le Loi", latitude=10.77, longitude=106.70)
    db_session.add(rest)
    await db_session.commit()

    item = MenuItem(restaurant_id=rest.id, name="Com Ga Saga", base_price=50000, stock_quantity=10, is_available=True)
    db_session.add(item)

    now = datetime.now(UTC)
    voucher = Voucher(
        code="SAGA10K",
        discount_type=DiscountType.FIXED,
        discount_value=10000,
        min_order_value=40000,
        usage_limit=5,
        used_count=0,
        is_active=True,
        valid_from=now - timedelta(days=1),
        valid_to=now + timedelta(days=30),
    )
    db_session.add(voucher)
    await db_session.commit()

    # 2. Tạo Customer & Đơn hàng
    _, c_headers = await _create_test_user(client, "cust_saga_ok@foodhub.com", "CUSTOMER")
    c_user = (await db_session.execute(select(User).where(User.email == "cust_saga_ok@foodhub.com"))).scalar_one()

    order = Order(
        order_code="ORD-SAGA-HAPPY",
        customer_id=c_user.id,
        restaurant_id=rest.id,
        delivery_address="10 Le Loi",
        delivery_lat=10.771,
        delivery_lng=106.701,
        subtotal=100000,
        delivery_fee=15000,
        discount_amount=10000,
        total_amount=105000,
        voucher_id=voucher.id,
        status=OrderStatus.SUBMITTED,
    )
    db_session.add(order)
    await db_session.commit()

    from app.models.order import OrderItem
    oi = OrderItem(order_id=order.id, menu_item_id=item.id, name_snapshot="Com Ga Saga", unit_price=50000, quantity=2, subtotal=100000)
    db_session.add(oi)
    await db_session.commit()

    # 3. Kích hoạt Saga Checkout
    saga_res = await client.post(
        f"/api/v1/orders/{order.id}/checkout-saga",
        headers=c_headers,
        json={"payment_method": "MOCK_WALLET", "simulate_payment_failure": False},
    )
    assert saga_res.status_code == 200
    res_data = saga_res.json()
    assert res_data["is_successful"] is True
    assert res_data["status"] == "COMPLETED"
    assert res_data["current_step"] == "DONE"

    # 4. Kiểm tra các hiệu ứng biên:
    # - Tồn kho giảm từ 10 xuống 8
    await db_session.refresh(item)
    assert item.stock_quantity == 8

    # - Voucher used_count tăng lên 1
    await db_session.refresh(voucher)
    assert voucher.used_count == 1

    # - VoucherUsage được tạo
    usage = (await db_session.execute(select(VoucherUsage).where(VoucherUsage.order_id == order.id))).scalar_one_or_none()
    assert usage is not None

    # - Đơn hàng chuyển sang MERCHANT_ACCEPTED
    await db_session.refresh(order)
    assert order.status == OrderStatus.MERCHANT_ACCEPTED

    # - Outbox Event ORDER_PAID được tạo
    evt = (await db_session.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == str(order.id), OutboxEvent.event_type == "ORDER_PAID"))).scalar_one_or_none()
    assert evt is not None


@pytest.mark.anyio
async def test_order_checkout_saga_compensation_on_payment_failure(client: AsyncClient, db_session: AsyncSession):
    """Kiểm tra khi thanh toán thất bại -> Tự động kích hoạt các giao dịch bù trừ (Compensations) hoàn kho và voucher."""
    # 1. Chuẩn bị Merchant, Quán, Món ăn (stock=10), Voucher
    _, m_headers = await _create_test_user(client, "merch_saga_fail@foodhub.com", "MERCHANT")
    m_user = (await db_session.execute(select(User).where(User.email == "merch_saga_fail@foodhub.com"))).scalar_one()

    rest = Restaurant(owner_id=m_user.id, name="Quan Saga Fail", address="5 Ham Nghi", latitude=10.77, longitude=106.70)
    db_session.add(rest)
    await db_session.commit()

    item = MenuItem(restaurant_id=rest.id, name="Pho Bo Saga", base_price=60000, stock_quantity=10, is_available=True)
    db_session.add(item)

    now = datetime.now(UTC)
    voucher = Voucher(
        code="FAIL10K",
        discount_type=DiscountType.FIXED,
        discount_value=10000,
        min_order_value=40000,
        usage_limit=5,
        used_count=0,
        is_active=True,
        valid_from=now - timedelta(days=1),
        valid_to=now + timedelta(days=30),
    )
    db_session.add(voucher)
    await db_session.commit()

    # 2. Tạo Customer & Đơn hàng
    _, c_headers = await _create_test_user(client, "cust_saga_fail@foodhub.com", "CUSTOMER")
    c_user = (await db_session.execute(select(User).where(User.email == "cust_saga_fail@foodhub.com"))).scalar_one()

    order = Order(
        order_code="ORD-SAGA-FAIL",
        customer_id=c_user.id,
        restaurant_id=rest.id,
        delivery_address="8 Ham Nghi",
        delivery_lat=10.771,
        delivery_lng=106.701,
        subtotal=60000,
        delivery_fee=15000,
        discount_amount=10000,
        total_amount=65000,
        voucher_id=voucher.id,
        status=OrderStatus.SUBMITTED,
    )
    db_session.add(order)
    await db_session.commit()

    from app.models.order import OrderItem
    oi = OrderItem(order_id=order.id, menu_item_id=item.id, name_snapshot="Pho Bo Saga", unit_price=60000, quantity=2, subtotal=120000)
    db_session.add(oi)
    await db_session.commit()

    # 3. Kích hoạt Saga với simulate_payment_failure = True
    saga_res = await client.post(
        f"/api/v1/orders/{order.id}/checkout-saga",
        headers=c_headers,
        json={"payment_method": "MOCK_WALLET", "simulate_payment_failure": True},
    )
    assert saga_res.status_code == 200
    res_data = saga_res.json()
    assert res_data["is_successful"] is False
    assert res_data["status"] == "FAILED"
    assert len(res_data["compensation_log"]) >= 1

    # 4. Kiểm tra các giao dịch bù trừ:
    # - Tồn kho món ăn được hoàn lại nguyên vẹn = 10
    await db_session.refresh(item)
    assert item.stock_quantity == 10

    # - Lượt dùng voucher được hoàn lại = 0
    await db_session.refresh(voucher)
    assert voucher.used_count == 0

    # - VoucherUsage không còn tồn tại
    usage = (await db_session.execute(select(VoucherUsage).where(VoucherUsage.order_id == order.id))).scalar_one_or_none()
    assert usage is None

    # - Đơn hàng chuyển sang CANCELLED
    await db_session.refresh(order)
    assert order.status == OrderStatus.CANCELLED

    # - Outbox Event ORDER_CANCELLED được ghi nhận
    evt = (await db_session.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == str(order.id), OutboxEvent.event_type == "ORDER_CANCELLED"))).scalar_one_or_none()
    assert evt is not None


@pytest.mark.anyio
async def test_admin_saga_and_outbox_endpoints(client: AsyncClient, db_session: AsyncSession):
    """Kiểm tra Admin tra cứu chi tiết Saga và các Outbox Events qua API."""
    # 1. Tạo Admin
    _, a_headers = await _create_test_user(client, "admin_saga_api@foodhub.com", "ADMIN")

    # 2. Tạo một SagaInstance trong DB
    saga = SagaInstance(
        saga_id="SAGA-TEST-API-001",
        saga_type="ORDER_CHECKOUT",
        order_id=123,
        status="COMPLETED",
        current_step="DONE",
        payload_json='{"order_id": 123}',
        compensation_log_json="[]",
    )
    db_session.add(saga)
    await db_session.commit()

    # 3. Admin tra cứu saga qua API
    saga_res = await client.get("/api/v1/admin/sagas/SAGA-TEST-API-001", headers=a_headers)
    assert saga_res.status_code == 200
    assert saga_res.json()["saga_id"] == "SAGA-TEST-API-001"
    assert saga_res.json()["status"] == "COMPLETED"

    # 4. Admin xem danh sách Outbox Events
    outbox_res = await client.get("/api/v1/admin/outbox/events", headers=a_headers)
    assert outbox_res.status_code == 200
    assert isinstance(outbox_res.json(), list)

    # 5. Admin kích hoạt relay qua API
    relay_res = await client.post("/api/v1/admin/outbox/relay", headers=a_headers)
    assert relay_res.status_code == 200
    assert "processed_count" in relay_res.json()
