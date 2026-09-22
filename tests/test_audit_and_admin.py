import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_admin_rbac_forbidden_for_non_admin(client: AsyncClient):
    """Kiểm tra chỉ có ADMIN mới được truy cập các endpoint quản trị."""
    # 1. Khách hàng
    await client.post("/api/v1/auth/register", json={
        "email": "cust_admin_test@foodhub.com",
        "password": "password123",
        "full_name": "Customer Admin Test",
        "role": "CUSTOMER"
    })
    c_token = (await client.post("/api/v1/auth/login", json={
        "email": "cust_admin_test@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    c_headers = {"Authorization": f"Bearer {c_token}"}

    res = await client.get("/api/v1/admin/orders", headers=c_headers)
    assert res.status_code == 403

    res_audit = await client.get("/api/v1/admin/audit-logs", headers=c_headers)
    assert res_audit.status_code == 403

    # 2. Chủ quán
    await client.post("/api/v1/auth/register", json={
        "email": "merch_admin_test@foodhub.com",
        "password": "password123",
        "full_name": "Merchant Admin Test",
        "role": "MERCHANT"
    })
    m_token = (await client.post("/api/v1/auth/login", json={
        "email": "merch_admin_test@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    m_headers = {"Authorization": f"Bearer {m_token}"}

    res_m = await client.get("/api/v1/admin/orders", headers=m_headers)
    assert res_m.status_code == 403


@pytest.mark.anyio
async def test_admin_refund_order_happy_path_and_audit_log(client: AsyncClient):
    """Admin hoàn tiền cho đơn hàng đã huỷ và tự động lưu vết vào AuditLog."""
    # 1. Tạo Admin
    await client.post("/api/v1/auth/register", json={
        "email": "super_admin@foodhub.com",
        "password": "password123",
        "full_name": "Super Admin",
        "role": "ADMIN"
    })
    admin_token = (await client.post("/api/v1/auth/login", json={
        "email": "super_admin@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 2. Tạo Merchant & Quán
    await client.post("/api/v1/auth/register", json={
        "email": "merch_refund@foodhub.com",
        "password": "password123",
        "full_name": "Merchant Refund",
        "role": "MERCHANT"
    })
    m_token = (await client.post("/api/v1/auth/login", json={
        "email": "merch_refund@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    m_headers = {"Authorization": f"Bearer {m_token}"}

    rest_res = await client.post("/api/v1/restaurants/", headers=m_headers, json={
        "name": "Pho Bo Refund Test",
        "address": "10 Ly Thuong Kiet",
        "latitude": 10.7700,
        "longitude": 106.6900
    })
    rest_id = rest_res.json()["id"]

    item_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=m_headers, json={
        "name": "Pho Dac Biet",
        "base_price": 60000,
        "stock_quantity": 20,
        "is_available": True
    })
    item_id = item_res.json()["id"]

    # 3. Tạo Customer & đặt đơn
    await client.post("/api/v1/auth/register", json={
        "email": "buyer_refund@foodhub.com",
        "password": "password123",
        "full_name": "Customer Refund",
        "role": "CUSTOMER"
    })
    c_token = (await client.post("/api/v1/auth/login", json={
        "email": "buyer_refund@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    c_headers = {"Authorization": f"Bearer {c_token}"}

    order_res = await client.post("/api/v1/orders/", headers=c_headers, json={
        "restaurant_id": rest_id,
        "items": [{"menu_item_id": item_id, "quantity": 1}],
        "delivery_lat": 10.7720,
        "delivery_lng": 106.6920,
        "delivery_address": "20 Pasteur"
    })
    order_id = order_res.json()["id"]
    total_amount = order_res.json()["total_amount"]

    # 4. Khách huỷ đơn khi còn ở trạng thái SUBMITTED
    cancel_res = await client.patch(
        f"/api/v1/orders/{order_id}/status",
        headers=c_headers,
        json={"new_status": "CANCELLED", "reason": "Đặt nhầm địa chỉ"}
    )
    assert cancel_res.status_code == 200

    # 5. Admin thực hiện hoàn tiền một phần (hoàn 50.000đ)
    refund_res = await client.post(
        f"/api/v1/admin/orders/{order_id}/refund",
        headers=admin_headers,
        json={"amount": 50000, "reason": "Hoàn tiền ví điện tử theo yêu cầu"}
    )
    assert refund_res.status_code == 200
    refund_data = refund_res.json()
    assert refund_data["order_id"] == order_id
    assert refund_data["refund_amount"] == 50000
    assert refund_data["status"] == "REFUNDED"

    # 6. Kiểm tra AuditLog được ghi lại
    audit_res = await client.get("/api/v1/admin/audit-logs", headers=admin_headers)
    assert audit_res.status_code == 200
    logs = audit_res.json()
    assert len(logs) >= 1

    # Tìm log vừa ghi cho order này
    order_log = next((log_item for log_item in logs if log_item["entity"] == "Order" and log_item["entity_id"] == order_id), None)
    assert order_log is not None
    assert order_log["action"] == "REFUND_ORDER"
    assert order_log["after_state"]["refund_amount"] == 50000
    assert order_log["before_state"]["total_amount"] == total_amount

    # 7. Admin xem danh sách toàn bộ đơn hàng
    all_orders_res = await client.get("/api/v1/admin/orders", headers=admin_headers)
    assert all_orders_res.status_code == 200
    all_orders = all_orders_res.json()
    assert any(o["id"] == order_id for o in all_orders)


@pytest.mark.anyio
async def test_admin_refund_invalid_conditions_rejected(client: AsyncClient):
    """Kiểm tra từ chối hoàn tiền khi trạng thái đơn không hợp lệ hoặc số tiền vượt mức."""
    # 1. Tạo Admin
    await client.post("/api/v1/auth/register", json={
        "email": "admin_validate@foodhub.com",
        "password": "password123",
        "full_name": "Admin Validate",
        "role": "ADMIN"
    })
    admin_token = (await client.post("/api/v1/auth/login", json={
        "email": "admin_validate@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 2. Tạo Merchant, Quán & Khách đặt đơn
    await client.post("/api/v1/auth/register", json={
        "email": "m_valid@foodhub.com",
        "password": "password123",
        "full_name": "Merchant Valid",
        "role": "MERCHANT"
    })
    m_token = (await client.post("/api/v1/auth/login", json={
        "email": "m_valid@foodhub.com",
        "password": "password123"
    })).json()["access_token"]

    rest_res = await client.post("/api/v1/restaurants/", headers={"Authorization": f"Bearer {m_token}"}, json={
        "name": "Bun Thit Nuong Valid",
        "address": "33 Nguyen Hue",
        "latitude": 10.7730,
        "longitude": 106.7030
    })
    rest_id = rest_res.json()["id"]

    item_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers={"Authorization": f"Bearer {m_token}"}, json={
        "name": "Bun Thit Nuong Cha Gio",
        "base_price": 40000,
        "stock_quantity": 20,
        "is_available": True
    })
    item_id = item_res.json()["id"]

    await client.post("/api/v1/auth/register", json={
        "email": "c_valid@foodhub.com",
        "password": "password123",
        "full_name": "Customer Valid",
        "role": "CUSTOMER"
    })
    c_token = (await client.post("/api/v1/auth/login", json={
        "email": "c_valid@foodhub.com",
        "password": "password123"
    })).json()["access_token"]

    order_res = await client.post("/api/v1/orders/", headers={"Authorization": f"Bearer {c_token}"}, json={
        "restaurant_id": rest_id,
        "items": [{"menu_item_id": item_id, "quantity": 1}],
        "delivery_lat": 10.7740,
        "delivery_lng": 106.7040,
        "delivery_address": "50 Nguyen Hue"
    })
    order_id = order_res.json()["id"]
    total_amount = order_res.json()["total_amount"]

    # Đơn vẫn đang ở SUBMITTED (chưa hủy) -> Cố tình hoàn tiền sẽ bị từ chối 400
    bad_res1 = await client.post(
        f"/api/v1/admin/orders/{order_id}/refund",
        headers=admin_headers,
        json={"reason": "Hoàn tiền đơn đang chờ"}
    )
    assert bad_res1.status_code == 400
    assert "Chỉ hoàn tiền cho đơn đã HUỶ hoặc GIAO THẤT BẠI" in bad_res1.json()["detail"]

    # Huỷ đơn
    await client.patch(
        f"/api/v1/orders/{order_id}/status",
        headers={"Authorization": f"Bearer {c_token}"},
        json={"new_status": "CANCELLED", "reason": "Khách đổi ý"}
    )

    # Cố tình hoàn tiền với số tiền lớn hơn giá trị đơn -> Bị từ chối 400
    bad_res2 = await client.post(
        f"/api/v1/admin/orders/{order_id}/refund",
        headers=admin_headers,
        json={"amount": total_amount + 99999, "reason": "Hoàn tiền vượt quá giá trị"}
    )
    assert bad_res2.status_code == 400
    assert "không thể lớn hơn" in bad_res2.json()["detail"]
