import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_order_creation_and_state_machine(client: AsyncClient):
    # 1. Tạo tài khoản Merchant & tạo nhà hàng kèm món ăn
    await client.post("/api/v1/auth/register", json={
        "email": "owner@foodhub.com",
        "password": "password123",
        "full_name": "Owner Com Tam",
        "role": "MERCHANT"
    })
    owner_login = await client.post("/api/v1/auth/login", json={
        "email": "owner@foodhub.com",
        "password": "password123"
    })
    owner_token = owner_login.json()["access_token"]
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    # Tạo quán
    rest_res = await client.post("/api/v1/restaurants/", headers=owner_headers, json={
        "name": "Com Tam Ba Ghien",
        "address": "84 Dang Van Ngu, Phu Nhuan",
        "latitude": 10.7928,
        "longitude": 106.6732
    })
    rest_id = rest_res.json()["id"]

    # Thêm món vào quán (Giá: 50.000đ, Tồn kho: 10)
    item_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=owner_headers, json={
        "name": "Com Suon Bi Cha",
        "description": "Suon nuong than cui",
        "base_price": 50000,
        "stock_quantity": 10,
        "is_available": True
    })
    item_id = item_res.json()["id"]

    # 2. Tạo tài khoản Customer & đăng nhập
    await client.post("/api/v1/auth/register", json={
        "email": "buyer@foodhub.com",
        "password": "password123",
        "full_name": "Nguyen Mua Hang",
        "role": "CUSTOMER"
    })
    buyer_login = await client.post("/api/v1/auth/login", json={
        "email": "buyer@foodhub.com",
        "password": "password123"
    })
    buyer_token = buyer_login.json()["access_token"]
    buyer_headers = {"Authorization": f"Bearer {buyer_token}"}

    # 3. Customer đặt đơn 2 phần cơm (Tổng tiền món = 100.000đ)
    order_res = await client.post("/api/v1/orders/", headers=buyer_headers, json={
        "restaurant_id": rest_id,
        "items": [
            {"menu_item_id": item_id, "quantity": 2}
        ],
        "delivery_lat": 10.7769,
        "delivery_lng": 106.7009,
        "delivery_address": "Chợ Bến Thành, Q1"
    })
    assert order_res.status_code == 201
    order_data = order_res.json()
    order_id = order_data["id"]

    assert order_data["subtotal"] == 100000
    assert order_data["delivery_fee"] >= 15000
    assert order_data["total_amount"] == order_data["subtotal"] + order_data["delivery_fee"]
    assert order_data["status"] == "SUBMITTED"
    assert len(order_data["items"]) == 1
    assert order_data["items"][0]["quantity"] == 2

    # 4. Kiểm tra tồn kho đã bị trừ từ 10 xuống 8
    menu_list = await client.get(f"/api/v1/menu/restaurants/{rest_id}/items")
    assert menu_list.status_code == 200
    updated_item = next(i for i in menu_list.json() if i["id"] == item_id)
    assert updated_item["stock_quantity"] == 8

    # 5. Customer xem danh sách đơn hàng (Kiểm tra chống N+1)
    my_orders_res = await client.get("/api/v1/orders/", headers=buyer_headers)
    assert my_orders_res.status_code == 200
    my_orders = my_orders_res.json()
    assert len(my_orders) >= 1
    assert my_orders[0]["items"][0]["unit_price"] == 50000

    # 6. Customer cố chuyển trạng thái sang MERCHANT_ACCEPTED (chỉ quán mới được duyệt) -> Phải bị từ chối 403 (RBAC)
    invalid_role_res = await client.patch(f"/api/v1/orders/{order_id}/status", headers=buyer_headers, json={
        "new_status": "MERCHANT_ACCEPTED"
    })
    assert invalid_role_res.status_code == 403

    # 7. Merchant duyệt đơn sang MERCHANT_ACCEPTED -> Thành công 200
    accept_res = await client.patch(f"/api/v1/orders/{order_id}/status", headers=owner_headers, json={
        "new_status": "MERCHANT_ACCEPTED",
        "reason": "Quán đã nhận đơn và bắt đầu chuẩn bị"
    })
    assert accept_res.status_code == 200
    assert accept_res.json()["status"] == "MERCHANT_ACCEPTED"

    # 8. Chuyển nhảy cóc bất hợp lệ (MERCHANT_ACCEPTED -> DELIVERED) -> Phải bị từ chối 400 (State Machine)
    skip_step_res = await client.patch(f"/api/v1/orders/{order_id}/status", headers=owner_headers, json={
        "new_status": "DELIVERED"
    })
    assert skip_step_res.status_code == 400

@pytest.mark.anyio
async def test_order_cancellation_restores_inventory(client: AsyncClient):
    # 1. Tạo tài khoản Merchant & tạo nhà hàng kèm món ăn
    await client.post("/api/v1/auth/register", json={
        "email": "owner_cancel@foodhub.com",
        "password": "password123",
        "full_name": "Owner Cancel Test",
        "role": "MERCHANT"
    })
    owner_login = await client.post("/api/v1/auth/login", json={
        "email": "owner_cancel@foodhub.com",
        "password": "password123"
    })
    owner_token = owner_login.json()["access_token"]
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    rest_res = await client.post("/api/v1/restaurants/", headers=owner_headers, json={
        "name": "Pho Hanoi Cancel",
        "address": "12 Hang Trong, Ha Noi",
        "latitude": 21.0307,
        "longitude": 105.8524
    })
    rest_id = rest_res.json()["id"]

    item_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=owner_headers, json={
        "name": "Pho Dac Biet",
        "description": "Pho bo truyen thong",
        "base_price": 60000,
        "stock_quantity": 10,
        "is_available": True
    })
    item_id = item_res.json()["id"]

    # 2. Đăng ký & đăng nhập tài khoản Customer
    await client.post("/api/v1/auth/register", json={
        "email": "buyer_cancel@foodhub.com",
        "password": "password123",
        "full_name": "Nguyen Cancel",
        "role": "CUSTOMER"
    })
    login_res = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "buyer_cancel@foodhub.com",
            "password": "password123"
        }
    )
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 3. Đặt 3 tô phở (tồn kho giảm từ 10 xuống 7)
    order_res = await client.post("/api/v1/orders/", headers=headers, json={
        "restaurant_id": rest_id,
        "items": [{"menu_item_id": item_id, "quantity": 3}],
        "delivery_lat": 21.0285,
        "delivery_lng": 105.8542,
        "delivery_address": "123 Le Loi, Q1"
    })
    assert order_res.status_code == 201
    order_id = order_res.json()["id"]

    # 4. Khách huỷ đơn ngay khi ở trạng thái SUBMITTED
    cancel_res = await client.patch(f"/api/v1/orders/{order_id}/status", headers=headers, json={
        "new_status": "CANCELLED",
        "reason": "Khách đổi ý muốn ăn món khác"
    })
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "CANCELLED"

    detail_res = await client.get(f"/api/v1/orders/{order_id}", headers=headers)
    assert detail_res.status_code == 200
    histories = detail_res.json()["histories"]
    assert len(histories) == 2  # Gồm 1 log lúc tạo + 1 log lúc huỷ

    # 5. Kiểm tra tồn kho của món xem đã được hoàn trả lại thành 10 chưa
    menu_res = await client.get(f"/api/v1/menu/restaurants/{rest_id}/items")
    item = next(i for i in menu_res.json() if i["id"] == item_id)
    assert item["stock_quantity"] == 10


@pytest.mark.anyio
async def test_dedicated_pay_and_cancel_endpoints(client: AsyncClient):
    """Kiểm thử 2 endpoint chuyên biệt POST /orders/{id}/pay và POST /orders/{id}/cancel."""
    # 1. Tạo Merchant & Quán
    await client.post("/api/v1/auth/register", json={
        "email": "m_pay_cancel@foodhub.com",
        "password": "password123",
        "full_name": "Merchant Pay Cancel",
        "role": "MERCHANT"
    })
    m_token = (await client.post("/api/v1/auth/login", json={
        "email": "m_pay_cancel@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    m_headers = {"Authorization": f"Bearer {m_token}"}

    rest_res = await client.post("/api/v1/restaurants/", headers=m_headers, json={
        "name": "Bun Bo Hue O Xuan",
        "address": "25 Le Duan",
        "latitude": 10.7812,
        "longitude": 106.6990
    })
    rest_id = rest_res.json()["id"]

    item_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=m_headers, json={
        "name": "Bun Bo Dac Biet",
        "base_price": 60000,
        "stock_quantity": 20,
        "is_available": True
    })
    item_id = item_res.json()["id"]

    # 2. Tạo Customer
    await client.post("/api/v1/auth/register", json={
        "email": "c_pay_cancel@foodhub.com",
        "password": "password123",
        "full_name": "Customer Pay Cancel",
        "role": "CUSTOMER"
    })
    c_token = (await client.post("/api/v1/auth/login", json={
        "email": "c_pay_cancel@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    c_headers = {"Authorization": f"Bearer {c_token}"}

    # 3. Đặt đơn 1 để test POST /pay
    order1_res = await client.post("/api/v1/orders/", headers=c_headers, json={
        "restaurant_id": rest_id,
        "items": [{"menu_item_id": item_id, "quantity": 1}],
        "delivery_lat": 10.7820,
        "delivery_lng": 106.7000,
        "delivery_address": "30 Le Duan"
    })
    order1_id = order1_res.json()["id"]
    assert order1_res.json()["status"] == "SUBMITTED"

    # Gọi POST /orders/{id}/pay
    pay_res = await client.post(
        f"/api/v1/orders/{order1_id}/pay",
        headers=c_headers,
        json={"payment_method": "MOCK_WALLET"}
    )
    assert pay_res.status_code == 200
    assert pay_res.json()["status"] == "MERCHANT_ACCEPTED"

    # Thanh toán lại lần 2 -> 400 Bad Request
    pay_again = await client.post(
        f"/api/v1/orders/{order1_id}/pay",
        headers=c_headers,
        json={"payment_method": "MOCK_WALLET"}
    )
    assert pay_again.status_code == 400

    # 4. Đặt đơn 2 để test POST /cancel
    order2_res = await client.post("/api/v1/orders/", headers=c_headers, json={
        "restaurant_id": rest_id,
        "items": [{"menu_item_id": item_id, "quantity": 2}],
        "delivery_lat": 10.7820,
        "delivery_lng": 106.7000,
        "delivery_address": "30 Le Duan"
    })
    order2_id = order2_res.json()["id"]

    # Gọi POST /orders/{id}/cancel
    cancel_res = await client.post(
        f"/api/v1/orders/{order2_id}/cancel",
        headers=c_headers,
        json={"reason": "Khach muon doi sang mon khac"}
    )
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "CANCELLED"