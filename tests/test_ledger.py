import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import LedgerAccountType, LedgerEntryType, OrderStatus
from app.models.order import Order
from app.models.restaurant import Restaurant
from app.models.user import User
from app.services.ledger_service import (
    record_order_refund,
    record_order_settlement,
)


@pytest.mark.anyio
async def test_ledger_settlement_double_entry_balance(db_session: AsyncSession):
    """Kiểm tra nguyên tắc bất biến kế toán: Tổng Nợ (DEBIT) == Tổng Có (CREDIT) khi quyết toán đơn hàng."""
    # 1. Tạo User & Quán giả lập
    user = User(
        email="ledger_cust@foodhub.com",
        hashed_password="hash",
        full_name="Ledger Customer",
        role="CUSTOMER",
    )
    merchant = User(
        email="ledger_merch@foodhub.com",
        hashed_password="hash",
        full_name="Ledger Merchant",
        role="MERCHANT",
    )
    db_session.add_all([user, merchant])
    await db_session.commit()

    rest = Restaurant(
        owner_id=merchant.id,
        name="Quan An So Cai",
        address="1 Pho Hue",
        latitude=21.0100,
        longitude=105.8500,
        is_open=True,
    )
    db_session.add(rest)
    await db_session.commit()

    # 2. Tạo đơn hàng: Món = 200.000, Ship = 20.000, Giảm = 30.000 -> Khách trả = 190.000
    order = Order(
        order_code="ORD-LEDGER-001",
        customer_id=user.id,
        restaurant_id=rest.id,
        delivery_address="10 Hang Bai",
        delivery_lat=21.0200,
        delivery_lng=105.8550,
        subtotal=200000,
        delivery_fee=20000,
        discount_amount=30000,
        total_amount=190000,
        status=OrderStatus.DELIVERED,
    )
    db_session.add(order)
    await db_session.commit()

    # 3. Ghi nhận bút toán sổ cái (hoa hồng sàn 15% -> 30.000đ)
    entries = await record_order_settlement(db=db_session, order=order, commission_rate=0.15)
    await db_session.commit()

    assert len(entries) == 3
    debit_sum = sum(e.amount for e in entries if e.entry_type == LedgerEntryType.DEBIT)
    credit_sum = sum(e.amount for e in entries if e.entry_type == LedgerEntryType.CREDIT)

    # Tổng DEBIT phải bằng chính xác Tổng CREDIT
    assert debit_sum == credit_sum == 190000

    # Chi tiết từng tài khoản
    cust_debit = next(e for e in entries if e.account_type == LedgerAccountType.CUSTOMER)
    rest_credit = next(e for e in entries if e.account_type == LedgerAccountType.RESTAURANT)
    plat_credit = next(e for e in entries if e.account_type == LedgerAccountType.PLATFORM)

    assert cust_debit.amount == 190000
    # Quán nhận: 200.000 - 30.000 (15%) = 170.000
    assert rest_credit.amount == 170000
    # Sàn nhận: 30.000 (hoa hồng) + 20.000 (ship) - 30.000 (voucher sàn tài trợ) = 20.000
    assert plat_credit.amount == 20000

    # 4. Kiểm tra tính Idempotent: gọi lại lần 2 không tạo thêm bản ghi trùng
    entries_again = await record_order_settlement(db=db_session, order=order, commission_rate=0.15)
    assert len(entries_again) == 3


@pytest.mark.anyio
async def test_ledger_refund_double_entry_balance(db_session: AsyncSession):
    """Kiểm tra nguyên tắc bất biến khi hoàn tiền: DEBIT PLATFORM == CREDIT CUSTOMER."""
    user = User(
        email="refund_cust@foodhub.com",
        hashed_password="hash",
        full_name="Refund Customer",
        role="CUSTOMER",
    )
    db_session.add(user)
    await db_session.commit()

    order = Order(
        order_code="ORD-REFUND-001",
        customer_id=user.id,
        restaurant_id=1,
        delivery_address="Address",
        delivery_lat=10.0,
        delivery_lng=106.0,
        subtotal=100000,
        delivery_fee=15000,
        discount_amount=0,
        total_amount=115000,
        status=OrderStatus.CANCELLED,
    )
    db_session.add(order)
    await db_session.commit()

    # Hoàn tiền 60.000đ
    entries = await record_order_refund(
        db=db_session,
        order=order,
        refund_amount=60000,
        reason="Khách khiếu nại món hỏng",
    )
    await db_session.commit()

    assert len(entries) == 2
    debit_entry = next(e for e in entries if e.entry_type == LedgerEntryType.DEBIT)
    credit_entry = next(e for e in entries if e.entry_type == LedgerEntryType.CREDIT)

    assert debit_entry.account_type == LedgerAccountType.PLATFORM
    assert debit_entry.amount == 60000

    assert credit_entry.account_type == LedgerAccountType.CUSTOMER
    assert credit_entry.amount == 60000


@pytest.mark.anyio
async def test_admin_view_order_ledger_flow(client: AsyncClient, db_session: AsyncSession):
    """
    Kiểm tra luồng thực tế qua API:
    1. Đăng ký Admin
    2. Tạo đơn hoàn tất DELIVERED -> tự động sinh sổ cái
    3. Admin gọi GET /api/v1/admin/orders/{order_id}/ledger
    4. Admin hoàn tiền đơn bị huỷ -> kiểm tra sinh bút toán hoàn tiền
    """
    # 1. Đăng ký Admin
    await client.post("/api/v1/auth/register", json={
        "email": "ledger_admin@foodhub.com",
        "password": "password123",
        "full_name": "Ledger Admin",
        "role": "ADMIN",
    })
    admin_token = (await client.post("/api/v1/auth/login", json={
        "email": "ledger_admin@foodhub.com",
        "password": "password123",
    })).json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 2. Tạo Merchant, Nhà hàng, Món ăn
    await client.post("/api/v1/auth/register", json={
        "email": "reconcile_merch@foodhub.com",
        "password": "password123",
        "full_name": "Reconcile Merchant",
        "role": "MERCHANT",
    })
    m_token = (await client.post("/api/v1/auth/login", json={
        "email": "reconcile_merch@foodhub.com",
        "password": "password123",
    })).json()["access_token"]
    m_headers = {"Authorization": f"Bearer {m_token}"}

    rest_res = await client.post("/api/v1/restaurants/", headers=m_headers, json={
        "name": "Nha Hang Doi Soat",
        "address": "100 Nguyen Van Linh",
        "latitude": 16.0600,
        "longitude": 108.2200,
    })
    rest_id = rest_res.json()["id"]

    item_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=m_headers, json={
        "name": "Mi Quang Dac Biet",
        "base_price": 50000,
        "stock_quantity": 40,
        "is_available": True,
    })
    item_id = item_res.json()["id"]

    # 3. Tạo Customer & đặt đơn
    await client.post("/api/v1/auth/register", json={
        "email": "reconcile_buyer@foodhub.com",
        "password": "password123",
        "full_name": "Buyer Reconcile",
        "role": "CUSTOMER",
    })
    c_token = (await client.post("/api/v1/auth/login", json={
        "email": "reconcile_buyer@foodhub.com",
        "password": "password123",
    })).json()["access_token"]
    c_headers = {"Authorization": f"Bearer {c_token}"}

    order_res = await client.post("/api/v1/orders/", headers=c_headers, json={
        "restaurant_id": rest_id,
        "items": [{"menu_item_id": item_id, "quantity": 2}],
        "delivery_lat": 16.0620,
        "delivery_lng": 108.2210,
        "delivery_address": "Cau Rong",
    })
    assert order_res.status_code == 201
    order_id = order_res.json()["id"]

    # 4. Chuyển trạng thái đơn sang DELIVERED
    await client.patch(f"/api/v1/orders/{order_id}/status", headers=m_headers, json={"new_status": "MERCHANT_ACCEPTED"})
    await client.patch(f"/api/v1/orders/{order_id}/status", headers=m_headers, json={"new_status": "PREPARING"})
    await client.patch(f"/api/v1/orders/{order_id}/status", headers=m_headers, json={"new_status": "READY_FOR_PICKUP"})

    # Driver nhận và giao
    await client.post("/api/v1/auth/register", json={
        "email": "shipper_ledger@foodhub.com",
        "password": "password123",
        "full_name": "Shipper Ledger",
        "role": "DRIVER",
    })
    d_token = (await client.post("/api/v1/auth/login", json={
        "email": "shipper_ledger@foodhub.com",
        "password": "password123",
    })).json()["access_token"]
    d_headers = {"Authorization": f"Bearer {d_token}"}

    await client.patch(f"/api/v1/orders/{order_id}/status", headers=d_headers, json={"new_status": "DRIVER_ASSIGNED"})
    await client.patch(f"/api/v1/orders/{order_id}/status", headers=d_headers, json={"new_status": "PICKED_UP"})
    deliver_res = await client.patch(f"/api/v1/orders/{order_id}/status", headers=d_headers, json={"new_status": "DELIVERED"})
    assert deliver_res.status_code == 200

    # 5. Admin kiểm tra sổ cái qua API
    ledger_res = await client.get(f"/api/v1/admin/orders/{order_id}/ledger", headers=admin_headers)
    assert ledger_res.status_code == 200
    ledger_entries = ledger_res.json()

    assert len(ledger_entries) == 3
    debits = [e for e in ledger_entries if e["entry_type"] == "DEBIT"]
    credits = [e for e in ledger_entries if e["entry_type"] == "CREDIT"]
    assert sum(d["amount"] for d in debits) == sum(c["amount"] for c in credits)

    # 6. Merchant xem sao kê đối soát (Reconciliation Statement)
    recon_res = await client.get(f"/api/v1/reports/reconciliation/restaurant/{rest_id}", headers=m_headers)
    assert recon_res.status_code == 200
    recon_data = recon_res.json()

    assert recon_data["restaurant_id"] == rest_id
    assert recon_data["total_settled_orders"] == 1
    assert recon_data["total_gross_food"] == 100000
    assert recon_data["total_commission_fee"] == 15000  # 15% của 100.000
    assert recon_data["total_net_payout"] == 85000       # Quán nhận 85.000
    assert len(recon_data["items"]) == 1

    # 7. Customer bị chặn xem sao kê đối soát (403 Forbidden)
    recon_forbidden = await client.get(f"/api/v1/reports/reconciliation/restaurant/{rest_id}", headers=c_headers)
    assert recon_forbidden.status_code == 403

