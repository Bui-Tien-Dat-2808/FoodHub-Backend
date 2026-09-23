import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_revenue_report_rbac(client: AsyncClient):
    """Kiểm tra phân quyền: Khách và Tài xế bị 403, Chủ quán và Admin được phép xem."""
    # 1. Khách hàng
    await client.post("/api/v1/auth/register", json={
        "email": "customer_rep@foodhub.com",
        "password": "password123",
        "full_name": "Customer Report",
        "role": "CUSTOMER"
    })
    c_token = (await client.post("/api/v1/auth/login", json={
        "email": "customer_rep@foodhub.com",
        "password": "password123"
    })).json()["access_token"]

    res_c = await client.get("/api/v1/reports/revenue", headers={"Authorization": f"Bearer {c_token}"})
    assert res_c.status_code == 403

    # 2. Tài xế
    await client.post("/api/v1/auth/register", json={
        "email": "driver_rep@foodhub.com",
        "password": "password123",
        "full_name": "Driver Report",
        "role": "DRIVER"
    })
    d_token = (await client.post("/api/v1/auth/login", json={
        "email": "driver_rep@foodhub.com",
        "password": "password123"
    })).json()["access_token"]

    res_d = await client.get("/api/v1/reports/revenue", headers={"Authorization": f"Bearer {d_token}"})
    assert res_d.status_code == 403

    # 3. Admin (khi chưa có đơn nào)
    await client.post("/api/v1/auth/register", json={
        "email": "admin_rep@foodhub.com",
        "password": "password123",
        "full_name": "Admin Report",
        "role": "ADMIN"
    })
    a_token = (await client.post("/api/v1/auth/login", json={
        "email": "admin_rep@foodhub.com",
        "password": "password123"
    })).json()["access_token"]

    res_a = await client.get("/api/v1/reports/revenue", headers={"Authorization": f"Bearer {a_token}"})
    assert res_a.status_code == 200
    data_a = res_a.json()
    assert data_a["total_orders"] == 0
    assert data_a["total_revenue"] == 0


@pytest.mark.anyio
async def test_revenue_report_aggregation_and_cache(client: AsyncClient):
    """Kiểm tra độ chính xác của SQL Aggregation, Top selling items, và Redis Cache-Aside."""
    # 1. Tạo Merchant, Quán và Món ăn
    await client.post("/api/v1/auth/register", json={
        "email": "owner_rep@foodhub.com",
        "password": "password123",
        "full_name": "Owner Report",
        "role": "MERCHANT"
    })
    m_token = (await client.post("/api/v1/auth/login", json={
        "email": "owner_rep@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    m_headers = {"Authorization": f"Bearer {m_token}"}

    rest_res = await client.post("/api/v1/restaurants/", headers=m_headers, json={
        "name": "Com Ga Hoi An Report",
        "address": "15 Tran Cao Van",
        "latitude": 15.8800,
        "longitude": 108.3380
    })
    rest_id = rest_res.json()["id"]

    # Thêm 2 món
    item1_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=m_headers, json={
        "name": "Com Ga Xay",
        "base_price": 50000,
        "stock_quantity": 50,
        "is_available": True
    })
    item1_id = item1_res.json()["id"]

    item2_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=m_headers, json={
        "name": "Goi Ga Bop",
        "base_price": 70000,
        "stock_quantity": 30,
        "is_available": True
    })
    item2_id = item2_res.json()["id"]

    # 2. Tạo Customer và đặt 2 đơn hàng
    await client.post("/api/v1/auth/register", json={
        "email": "buyer_rep1@foodhub.com",
        "password": "password123",
        "full_name": "Buyer 1",
        "role": "CUSTOMER"
    })
    b1_token = (await client.post("/api/v1/auth/login", json={
        "email": "buyer_rep1@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    b1_headers = {"Authorization": f"Bearer {b1_token}"}

    # Đơn 1: 2 phần Cơm Gà (subtotal = 100.000, fee = 15.000, total = 115.000)
    o1_res = await client.post("/api/v1/orders/", headers=b1_headers, json={
        "restaurant_id": rest_id,
        "items": [{"menu_item_id": item1_id, "quantity": 2}],
        "delivery_lat": 15.8810,
        "delivery_lng": 108.3390,
        "delivery_address": "Chùa Cầu"
    })
    o1_id = o1_res.json()["id"]
    o1_fee = o1_res.json()["delivery_fee"]

    # Đơn 2: 1 phần Gỏi Gà (subtotal = 70.000, fee = 15.000, total = 85.000)
    # Đơn 2: 1 phần Gỏi Gà
    o2_res = await client.post("/api/v1/orders/", headers=b1_headers, json={
        "restaurant_id": rest_id,
        "items": [{"menu_item_id": item2_id, "quantity": 1}],
        "delivery_lat": 15.8810,
        "delivery_lng": 108.3390,
        "delivery_address": "Chùa Cầu"
    })
    o2_id = o2_res.json()["id"]
    o2_fee = o2_res.json()["delivery_fee"]

    # 3. Chuyển Đơn 1 sang DELIVERED (SUBMITTED -> MERCHANT_ACCEPTED -> READY_FOR_PICKUP -> PICKED_UP -> DELIVERED)
    # Lần lượt theo State Machine FSM:
    await client.patch(f"/api/v1/orders/{o1_id}/status", headers=m_headers, json={"new_status": "MERCHANT_ACCEPTED"})
    await client.patch(f"/api/v1/orders/{o1_id}/status", headers=m_headers, json={"new_status": "PREPARING"})
    await client.patch(f"/api/v1/orders/{o1_id}/status", headers=m_headers, json={"new_status": "READY_FOR_PICKUP"})

    # Tạo Driver để nhận đơn và giao
    await client.post("/api/v1/auth/register", json={
        "email": "driver_deliv@foodhub.com",
        "password": "password123",
        "full_name": "Driver Delivery",
        "role": "DRIVER"
    })
    drv_token = (await client.post("/api/v1/auth/login", json={
        "email": "driver_deliv@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    drv_headers = {"Authorization": f"Bearer {drv_token}"}

    res = await client.patch(f"/api/v1/orders/{o1_id}/status", headers=drv_headers, json={"new_status": "DRIVER_ASSIGNED"})
    assert res.status_code == 200
    res = await client.patch(f"/api/v1/orders/{o1_id}/status", headers=drv_headers, json={"new_status": "PICKED_UP"})
    assert res.status_code == 200
    res = await client.patch(f"/api/v1/orders/{o1_id}/status", headers=drv_headers, json={"new_status": "DELIVERED"})
    assert res.status_code == 200

    # 4. Merchant xem báo cáo doanh thu lần 1 (Cache Miss -> query DB)
    rep1_res = await client.get("/api/v1/reports/revenue", headers=m_headers)
    assert rep1_res.status_code == 200
    rep1 = rep1_res.json()

    assert rep1["total_orders"] == 1
    assert rep1["total_food_amount"] == 100000
    assert rep1["total_delivery_fees"] == o1_fee
    assert rep1["total_revenue"] == 100000 + o1_fee
    assert rep1["average_order_value"] == float(100000 + o1_fee)

    assert len(rep1["top_selling_items"]) == 1
    assert rep1["top_selling_items"][0]["menu_item_id"] == item1_id
    assert rep1["top_selling_items"][0]["total_quantity"] == 2

    # 5. Gọi lại lần 2 -> Kiểm tra Cache Hit trả về cùng kết quả
    rep2_res = await client.get("/api/v1/reports/revenue", headers=m_headers)
    assert rep2_res.status_code == 200
    assert rep2_res.json()["total_orders"] == 1

    # 6. Chuyển tiếp Đơn 2 sang DELIVERED -> Kiểm tra Cache Invalidation
    await client.patch(f"/api/v1/orders/{o2_id}/status", headers=m_headers, json={"new_status": "MERCHANT_ACCEPTED"})
    await client.patch(f"/api/v1/orders/{o2_id}/status", headers=m_headers, json={"new_status": "PREPARING"})
    await client.patch(f"/api/v1/orders/{o2_id}/status", headers=m_headers, json={"new_status": "READY_FOR_PICKUP"})
    res = await client.patch(f"/api/v1/orders/{o2_id}/status", headers=drv_headers, json={"new_status": "DRIVER_ASSIGNED"})
    assert res.status_code == 200
    res = await client.patch(f"/api/v1/orders/{o2_id}/status", headers=drv_headers, json={"new_status": "PICKED_UP"})
    assert res.status_code == 200
    res = await client.patch(f"/api/v1/orders/{o2_id}/status", headers=drv_headers, json={"new_status": "DELIVERED"})
    assert res.status_code == 200

    # 7. Sau khi đơn 2 giao xong, cache đã bị invalidate -> Báo cáo phải cập nhật 2 đơn!
    rep3_res = await client.get("/api/v1/reports/revenue", headers=m_headers)
    assert rep3_res.status_code == 200
    rep3 = rep3_res.json()

    total_expected_fees = o1_fee + o2_fee
    total_expected_revenue = 170000 + total_expected_fees

    assert rep3["total_orders"] == 2
    assert rep3["total_revenue"] == total_expected_revenue
    assert rep3["total_food_amount"] == 170000
    assert rep3["total_delivery_fees"] == total_expected_fees
    assert rep3["average_order_value"] == float(total_expected_revenue / 2)
    assert len(rep3["top_selling_items"]) == 2
