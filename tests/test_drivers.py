import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.driver import DriverProfile
from app.models.enums import OrderStatus
from app.models.order import Order
from app.models.user import User


@pytest.mark.anyio
async def test_driver_profile_and_status(client: AsyncClient, db_session: AsyncSession):
    """Test lấy profile tài xế và bật/tắt trạng thái online."""
    # 1. Đăng ký tài xế
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "driver_status_test@foodhub.com",
            "password": "password123",
            "full_name": "Nguyen Van Shipper",
            "role": "DRIVER",
        },
    )
    login_res = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "driver_status_test@foodhub.com",
            "password": "password123",
        },
    )
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Tạo DriverProfile trong DB
    driver_user = (
        await db_session.execute(
            select(User).where(User.email == "driver_status_test@foodhub.com")
        )
    ).scalar_one()
    profile = DriverProfile(
        user_id=driver_user.id,
        license_plate="29-E1 12345",
        is_online=False,
        is_busy=False,
    )
    db_session.add(profile)
    await db_session.commit()

    # 2. Lấy profile
    get_res = await client.get("/api/v1/driver/profile", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["license_plate"] == "29-E1 12345"
    assert get_res.json()["is_online"] is False

    # 3. Bật online
    patch_res = await client.patch(
        "/api/v1/driver/status",
        headers=headers,
        json={"is_online": True},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["is_online"] is True

    # 4. Kiểm tra lại qua GET profile
    get_res2 = await client.get("/api/v1/driver/profile", headers=headers)
    assert get_res2.json()["is_online"] is True


@pytest.mark.anyio
async def test_driver_accept_delivery_flow(client: AsyncClient, db_session: AsyncSession):
    """Test luồng tài xế nhận đơn hàng (accept delivery) và chống tranh chấp (409 Conflict)."""
    # 1. Tạo Merchant & Nhà hàng
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "merchant_delivery@foodhub.com",
            "password": "password123",
            "full_name": "Merchant Delivery",
            "role": "MERCHANT",
        },
    )
    m_token = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "merchant_delivery@foodhub.com", "password": "password123"},
        )
    ).json()["access_token"]
    m_headers = {"Authorization": f"Bearer {m_token}"}

    rest_res = await client.post(
        "/api/v1/restaurants/",
        headers=m_headers,
        json={
            "name": "Com Tam Cali",
            "address": "45 Nguyen Trai",
            "latitude": 10.768,
            "longitude": 106.690,
        },
    )
    rest_id = rest_res.json()["id"]

    item_res = await client.post(
        f"/api/v1/menu/restaurants/{rest_id}/items",
        headers=m_headers,
        json={
            "name": "Com Suon Bi Cha",
            "base_price": 55000,
            "stock_quantity": 50,
            "is_available": True,
        },
    )
    item_id = item_res.json()["id"]

    # 2. Tạo Customer & đặt đơn
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "customer_delivery@foodhub.com",
            "password": "password123",
            "full_name": "Customer Delivery",
            "role": "CUSTOMER",
        },
    )
    c_token = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "customer_delivery@foodhub.com", "password": "password123"},
        )
    ).json()["access_token"]
    c_headers = {"Authorization": f"Bearer {c_token}"}

    order_res = await client.post(
        "/api/v1/orders/",
        headers=c_headers,
        json={
            "restaurant_id": rest_id,
            "items": [{"menu_item_id": item_id, "quantity": 1}],
            "delivery_lat": 10.770,
            "delivery_lng": 106.692,
            "delivery_address": "80 Nguyen Trai",
        },
    )
    order_id = order_res.json()["id"]

    # 3. Tạo 2 tài xế Driver 1 và Driver 2
    for email, plate in [
        ("shipper1@foodhub.com", "59-A1 11111"),
        ("shipper2@foodhub.com", "59-A2 22222"),
    ]:
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "password123",
                "full_name": f"Driver {plate}",
                "role": "DRIVER",
            },
        )
        user = (
            await db_session.execute(select(User).where(User.email == email))
        ).scalar_one()
        p = DriverProfile(
            user_id=user.id,
            license_plate=plate,
            is_online=True,
            is_busy=False,
        )
        db_session.add(p)
    await db_session.commit()

    d1_token = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "shipper1@foodhub.com", "password": "password123"},
        )
    ).json()["access_token"]
    d1_headers = {"Authorization": f"Bearer {d1_token}"}

    d2_token = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "shipper2@foodhub.com", "password": "password123"},
        )
    ).json()["access_token"]
    d2_headers = {"Authorization": f"Bearer {d2_token}"}

    # 4. Khi đơn chưa ở READY_FOR_PICKUP (đang SUBMITTED), Driver nhận sẽ bị lỗi 400
    fail_res = await client.post(
        f"/api/v1/driver/deliveries/{order_id}/accept",
        headers=d1_headers,
    )
    assert fail_res.status_code == 400
    assert "Không thể nhận đơn hàng" in fail_res.json()["detail"]

    # 5. Quán đẩy trạng thái đơn: SUBMITTED -> MERCHANT_ACCEPTED -> PREPARING -> READY_FOR_PICKUP
    await client.patch(
        f"/api/v1/orders/{order_id}/status",
        headers=m_headers,
        json={"new_status": "MERCHANT_ACCEPTED"},
    )
    await client.patch(
        f"/api/v1/orders/{order_id}/status",
        headers=m_headers,
        json={"new_status": "PREPARING"},
    )
    await client.patch(
        f"/api/v1/orders/{order_id}/status",
        headers=m_headers,
        json={"new_status": "READY_FOR_PICKUP"},
    )

    # 6. Driver 1 nhận đơn -> Thành công
    accept_res = await client.post(
        f"/api/v1/driver/deliveries/{order_id}/accept",
        headers=d1_headers,
    )
    assert accept_res.status_code == 200
    assert accept_res.json()["order_id"] == order_id
    assert accept_res.json()["status"] == "ACCEPTED"

    # Kiểm tra trạng thái đơn đã đổi sang DRIVER_ASSIGNED
    order_db = (
        await db_session.execute(select(Order).where(Order.id == order_id))
    ).scalar_one()
    assert order_db.status == OrderStatus.DRIVER_ASSIGNED

    # 7. Driver 2 cùng tranh nhận đơn đó -> Bị 409 CONFLICT
    conflict_res = await client.post(
        f"/api/v1/driver/deliveries/{order_id}/accept",
        headers=d2_headers,
    )
    assert conflict_res.status_code in (400, 409)

