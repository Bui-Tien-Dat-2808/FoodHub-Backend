import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.order import Order


@pytest.mark.anyio
async def test_order_creation_idempotency(client: AsyncClient):
    """
        Gửi 2 request đặt hàng liên tiếp với cùng 1 header X-Idempotency-Key.
        Kỳ vọng:
        - Request 1: Tạo đơn thành công, trừ kho 10 -> 8
        - Request 2: Trả về kết quả đơn cũ (Order ID cũ)
        - Tồn kho KHÔNG bị trừ lần 2 (vẫn là 8)
        - Tổng số đơn trong DB chỉ đúng bằng 1 đơn
    """
    # 1. Tạo Merchant, Quán & Món ăn
    await client.post("/api/v1/auth/register", json={
        "email": "owner_idem@foodhub.com",
        "password": "password123",
        "full_name": "Merchant Item",
        "role": "MERCHANT"
    })
    m_login = await client.post("/api/v1/auth/login", json={
        "email": "owner_idem@foodhub.com",
        "password": "password123"
    })
    m_headers = {"Authorization": f"Bearer {m_login.json()['access_token']}"}

    rest_res = await client.post("/api/v1/restaurants/", headers=m_headers, json={
        "name": "Quan An Ba Tu",
        "address": "21 Hai Ba Trung",
        "latitude": 10.7800,
        "longitude": 106.6950
    })
    rest_id = rest_res.json()["id"]

    item_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=m_headers, json={
        "name": "Banh Mi Cha Lua",
        "base_price": 30000,
        "stock_quantity": 10,
        "is_available": True
    })
    item_id = item_res.json()["id"]

    # 2. Customer & Đăng nhập
    await client.post("/api/v1/auth/register", json={
        "email": "buyer_idem@foodhub.com",
        "password": "password123",
        "full_name": "Customer Idem",
        "role": "CUSTOMER"
    })
    c_login = await client.post("/api/v1/auth/login", json={
        "email": "buyer_idem@foodhub.com",
        "password": "password123"
    })
    buyer_headers = {
        "Authorization": f"Bearer {c_login.json()['access_token']}",
        "X-Idempotency-Key": "unique-idempotency-key-12345"
    }

    order_payload = {
        "restaurant_id": rest_id,
        "items": [{"menu_item_id": item_id, "quantity": 2}],
        "delivery_lat": 10.7820,
        "delivery_lng": 106.6980,
        "delivery_address": "100 Nguyen Dinh Chieu"
    }

    # 3. Gửi Request lần 1
    res_1 = await client.post("/api/v1/orders/", headers=buyer_headers, json=order_payload)
    assert res_1.status_code == 201
    order_1 = res_1.json()

    # 4. Gửi Request lần 2 - Client bấm nhầm lần 2 với cùng key
    res_2 = await client.post("/api/v1/orders/", headers=buyer_headers, json=order_payload)
    assert res_2.status_code in (200, 201)
    order_2 = res_2.json()

    assert order_1["id"] == order_2["id"]
    assert order_1["order_code"] == order_2["order_code"]

    menu_res = await client.get(f"/api/v1/menu/restaurants/{rest_id}/items")
    item = next(i for i in menu_res.json() if i["id"] == item_id)
    assert item["stock_quantity"] == 8

    from tests.conftest import TestSessionLocal
    async with TestSessionLocal() as session:
        orders = (await session.execute(select(Order))).scalars().all()
        assert len(orders) == 1