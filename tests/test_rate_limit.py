import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_order_rate_limit_exceeded(client: AsyncClient):
    """Bắn liên tiếp 6 request tạo đơn. Request thứ 6 phải bị chặn với mã 429 Too Many Requests."""
    # 1. Tạo Merchant & Nhà hàng & Món ăn
    await client.post("/api/v1/auth/register", json={
        "email": "owner_rl@foodhub.com",
        "password": "password123",
        "full_name": "Merchant RateLimit",
        "role": "MERCHANT"
    })
    m_login = await client.post("/api/v1/auth/login", json={
        "email": "owner_rl@foodhub.com",
        "password": "password123"
    })
    m_headers = {"Authorization": f"Bearer {m_login.json()['access_token']}"}

    rest_res = await client.post("/api/v1/restaurants/", headers=m_headers, json={
        "name": "Quan Com RateLimit",
        "address": "99 Nguyen Trai",
        "latitude": 10.7600,
        "longitude": 106.6900
    })
    rest_id = rest_res.json()["id"]

    item_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=m_headers, json={
        "name": "Com Suon Bi",
        "base_price": 40000,
        "stock_quantity": 100,
        "is_available": True
    })
    item_id = item_res.json()["id"]

    # 2. Tạo Customer
    await client.post("/api/v1/auth/register", json={
        "email": "buyer_rl@foodhub.com",
        "password": "password123",
        "full_name": "Customer RateLimit",
        "role": "CUSTOMER"
    })
    c_login = await client.post("/api/v1/auth/login", json={
        "email": "buyer_rl@foodhub.com",
        "password": "password123"
    })
    headers = {"Authorization": f"Bearer {c_login.json()['access_token']}"}

    payload = {
        "restaurant_id": rest_id,
        "items": [{"menu_item_id": item_id, "quantity": 1}],
        "delivery_lat": 10.7620,
        "delivery_lng": 106.6920,
        "delivery_address": "456 Le Lai"
    }

    # 3. Gửi liên tiếp 5 đơn hàng -> Phải thành công (201)
    for i in range(5):
        res = await client.post("/api/v1/orders/", headers=headers, json=payload)
        assert res.status_code == 201

    # 4. Gửi đơn thứ 6 -> Vượt quá 5 đơn/phút -> Phải bị chặn 429
    res_6 = await client.post("/api/v1/orders/", headers=headers, json=payload)
    assert res_6.status_code == 429
    assert "Retry-After" in res_6.headers


@pytest.mark.anyio
async def test_login_rate_limit_exceeded(client: AsyncClient):
    """Thử đăng nhập sai mật khẩu 6 lần liên tiếp. Lần thứ 6 phải bị chặn với mã 429."""
    # 5 lần đầu báo sai mật khẩu (401)
    for _ in range(5):
        res = await client.post("/api/v1/auth/login", json={
            "email": "wrong_user@foodhub.com",
            "password": "wrongpassword"
        })
        assert res.status_code == 401

    # Lần thứ 6 bị chặn vì quá giới hạn thử (429)
    res_6 = await client.post("/api/v1/auth/login", json={
        "email": "wrong_user@foodhub.com",
        "password": "wrongpassword"
    })
    assert res_6.status_code == 429
    assert "Retry-After" in res_6.headers