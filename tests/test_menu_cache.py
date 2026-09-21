import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_menu_cache_aside_and_invalidation(client: AsyncClient):
    """
        Quy trình kiểm thử:
        1. Quán tạo món
        2. Khách xem menu lần 1 -> Đọc DB và đưa vào Redis cache (Cache Miss)
        3. Khách xem menu lần 2 -> Đọc từ Redis cache (Cache Hit)
        4. Quán tăng giá -> Cache bị xoá (Invalidation)
        5. Khách xem menu lần 3 -> Đọc giá mới từ DB và đưa vào Cache
    """
    # 1. Tạo Merchant & Nhà hàng
    await client.post("/api/v1/auth/register", json= {
        "email": "owner_cache@foodhub.com",
        "password": "password123",
        "full_name": "Merchant Cache Test",
        "role": "MERCHANT"
    })
    m_login = await client.post("/api/v1/auth/login", json={
        "email": "owner_cache@foodhub.com",
        "password": "password123"
    })
    m_headers = {"Authorization": f"Bearer {m_login.json()['access_token']}"}

    rest_res = await client.post("/api/v1/restaurants/", headers=m_headers, json={
        "name": "Pho Thin Ha Noi",
        "address": "13 Lo Duc",
        "latitude": 21.0180,
        "longitude": 105.8560
    })
    rest_id = rest_res.json()["id"]

    # Tạo món
    item_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=m_headers, json={
        "name": "Pho Bo Tai Lan",
        "base_price": 45000,
        "stock_quantity": 50,
        "is_available": True
    })
    item_id = item_res.json()["id"]

    # 2. Khách xem menu lần 1
    menu_1 = await client.get(f"/api/v1/menu/restaurants/{rest_id}/items")
    assert menu_1.status_code == 200
    items_1 = menu_1.json()
    assert len(items_1) == 1
    assert items_1[0]["base_price"] == 45000

    # 3. Khách xem menu lần 2
    menu_2 = await client.get(f"/api/v1/menu/restaurants/{rest_id}/items")
    assert menu_2.status_code == 200
    assert menu_2.json()[0]["base_price"] == 45000

    # 4. Quán cập nhật giá mới
    update_res = await client.patch(f"/api/v1/menu/items/{item_id}", headers=m_headers, json={
        "base_price": 55000
    })
    assert update_res.status_code == 200
    assert update_res.json()["base_price"] == 55000

    # 5. Khách xem menu lần 3
    menu_3 = await client.get(f"/api/v1/menu/restaurants/{rest_id}/items")
    assert menu_3.status_code == 200
    items_3 = menu_3.json()
    assert items_3[0]["base_price"] == 55000