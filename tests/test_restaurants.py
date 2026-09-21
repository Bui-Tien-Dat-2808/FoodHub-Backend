import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_create_and_get_restaurant(client: AsyncClient):
    # 1. Đăng ký tài khoản Merchant
    await client.post("/api/v1/auth/register", json={
        "email": "merchant_test@foodhub.com",
        "password": "password123",
        "full_name": "Merchant Test",
        "role": "MERCHANT"
    })
    login_res = await client.post("/api/v1/auth/login", json={
        "email": "merchant_test@foodhub.com",
        "password": "password123"
    })
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Tạo nhà hàng mới
    create_res = await client.post("/api/v1/restaurants/", headers=headers, json={
        "name": "Quan Com Tam Sai Gon",
        "address": "456 Le Loi, Q1",
        "latitude": 10.7769,
        "longitude": 106.7009
    })
    assert create_res.status_code == 201
    rest_data = create_res.json()
    restaurant_id = rest_data["id"]

    # 3. Lấy thông tin nhà hàng theo ID -> Kỳ vọng HTTP 200 (không bị lỗi 500)
    get_res = await client.get(f"/api/v1/restaurants/{restaurant_id}")
    assert get_res.status_code == 200
    assert get_res.json()["name"] == "Quan Com Tam Sai Gon"

    # 4. Lấy nhà hàng không tồn tại -> Kỳ vọng HTTP 404
    not_found_res = await client.get("/api/v1/restaurants/999999")
    assert not_found_res.status_code == 404
