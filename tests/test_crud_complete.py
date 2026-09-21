import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_full_crud_operations(client: AsyncClient):
    # 1. Đăng ký tài khoản Admin và Merchant
    await client.post("/api/v1/auth/register", json={
        "email": "crud_admin@foodhub.com",
        "password": "password123",
        "full_name": "CRUD Admin",
        "role": "ADMIN"
    })
    admin_login = await client.post("/api/v1/auth/login", json={
        "email": "crud_admin@foodhub.com",
        "password": "password123"
    })
    admin_token = admin_login.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    await client.post("/api/v1/auth/register", json={
        "email": "crud_merchant@foodhub.com",
        "password": "password123",
        "full_name": "CRUD Merchant",
        "role": "MERCHANT"
    })
    merchant_login = await client.post("/api/v1/auth/login", json={
        "email": "crud_merchant@foodhub.com",
        "password": "password123"
    })
    merchant_token = merchant_login.json()["access_token"]
    merchant_headers = {"Authorization": f"Bearer {merchant_token}"}

    # 2. Test User CRUD: Admin xem danh sách user
    users_res = await client.get("/api/v1/users/", headers=admin_headers)
    assert users_res.status_code == 200
    assert len(users_res.json()) >= 2

    # Merchant cập nhật thông tin cá nhân của mình
    me_res = await client.get("/api/v1/auth/me", headers=merchant_headers)
    merchant_id = me_res.json()["id"]
    update_user_res = await client.patch(f"/api/v1/users/{merchant_id}", headers=merchant_headers, json={
        "full_name": "CRUD Merchant Updated",
        "phone": "0987654321"
    })
    assert update_user_res.status_code == 200
    assert update_user_res.json()["full_name"] == "CRUD Merchant Updated"

    # 3. Test Restaurant CRUD: Tạo & cập nhật nhà hàng
    rest_res = await client.post("/api/v1/restaurants/", headers=merchant_headers, json={
        "name": "Tiem Banh Mi",
        "address": "12 Hang Bac, Ha Noi",
        "latitude": 21.0333,
        "longitude": 105.8500
    })
    assert rest_res.status_code == 201
    rest_id = rest_res.json()["id"]

    # Cập nhật nhà hàng
    update_rest_res = await client.patch(f"/api/v1/restaurants/{rest_id}", headers=merchant_headers, json={
        "name": "Tiem Banh Mi Pho Co",
        "is_open": False
    })
    assert update_rest_res.status_code == 200
    assert update_rest_res.json()["name"] == "Tiem Banh Mi Pho Co"
    assert update_rest_res.json()["is_open"] is False

    # 4. Test Menu CRUD: Tạo, xem chi tiết, cập nhật và xóa món ăn
    item_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=merchant_headers, json={
        "name": "Banh Mi Thit Nuong",
        "description": "Banh mi pate thit nuong",
        "base_price": 25000,
        "stock_quantity": 50,
        "is_available": True
    })
    assert item_res.status_code == 201
    item_id = item_res.json()["id"]

    # Xem chi tiết món
    detail_res = await client.get(f"/api/v1/menu/items/{item_id}")
    assert detail_res.status_code == 200
    assert detail_res.json()["name"] == "Banh Mi Thit Nuong"

    # Cập nhật món (đổi giá sang 30.000đ)
    update_item_res = await client.patch(f"/api/v1/menu/items/{item_id}", headers=merchant_headers, json={
        "base_price": 30000,
        "stock_quantity": 40
    })
    assert update_item_res.status_code == 200
    assert update_item_res.json()["base_price"] == 30000
    assert update_item_res.json()["stock_quantity"] == 40

    # Xóa món ăn khỏi quán
    del_res = await client.delete(f"/api/v1/menu/items/{item_id}", headers=merchant_headers)
    assert del_res.status_code == 204

    # Kiểm tra lại: Món đã bị xóa
    after_del = await client.get(f"/api/v1/menu/items/{item_id}")
    assert after_del.status_code == 404
