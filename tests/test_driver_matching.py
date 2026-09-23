import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.driver import DriverAssignment, DriverProfile
from app.models.enums import DriverAssignmentStatus, OrderStatus
from app.models.order import Order
from app.models.restaurant import MenuItem, Restaurant
from app.models.user import User
from app.services.geo_service import haversine_distance


def test_haversine_formula():
    """Kiểm tra độ chính xác của hàm tính khoảng cách Haversine."""
    # Điểm trùng nhau
    assert haversine_distance(10.7769, 106.7009, 10.7769, 106.7009) == 0.0
    # Chợ Bến Thành (10.7725, 106.6980) đến Landmark 81 (10.7950, 106.7219) ~ 3.5 - 3.7 km
    dist = haversine_distance(10.7725, 106.6980, 10.7950, 106.7219)
    assert 3.0 <= dist <= 4.0


@pytest.mark.anyio
async def test_find_nearby_available_drivers(client: AsyncClient, db_session: AsyncSession):
    """
    Kiểm tra endpoint GET /api/v1/driver/nearby:
    - Chỉ lấy tài xế online=True, busy=False.
    - Chỉ lấy tài xế trong bán kính quy định.
    - Sắp xếp tăng dần theo khoảng cách.
    """
    # 1. Tạo Merchant và login để lấy token
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "merchant_geo@foodhub.com",
            "password": "password123",
            "full_name": "Merchant Geo Test",
            "role": "MERCHANT",
        },
    )
    m_token = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "merchant_geo@foodhub.com", "password": "password123"},
        )
    ).json()["access_token"]
    m_headers = {"Authorization": f"Bearer {m_token}"}

    # Tâm tìm kiếm (Nhà hàng): 10.7700, 106.6900
    center_lat, center_lng = 10.7700, 106.6900

    # 2. Tạo 4 tài xế với các trạng thái khác nhau
    # Driver A: Gần (cách ~0.8 km), Online, Rảnh
    user_a = User(
        email="driver_a@foodhub.com",
        hashed_password="hash",
        full_name="Driver A (Gần)",
        phone="0901000001",
        role="DRIVER",
    )
    # Driver B: Xa (cách ~20 km), Online, Rảnh
    user_b = User(
        email="driver_b@foodhub.com",
        hashed_password="hash",
        full_name="Driver B (Xa)",
        phone="0901000002",
        role="DRIVER",
    )
    # Driver C: Gần (cách ~0.5 km), Online nhưng BẬN (is_busy=True)
    user_c = User(
        email="driver_c@foodhub.com",
        hashed_password="hash",
        full_name="Driver C (Bận)",
        phone="0901000003",
        role="DRIVER",
    )
    # Driver D: Gần (cách ~0.3 km), Rảnh nhưng OFFLINE (is_online=False)
    user_d = User(
        email="driver_d@foodhub.com",
        hashed_password="hash",
        full_name="Driver D (Offline)",
        phone="0901000004",
        role="DRIVER",
    )
    db_session.add_all([user_a, user_b, user_c, user_d])
    await db_session.commit()

    prof_a = DriverProfile(
        user_id=user_a.id,
        license_plate="59-A1 1111",
        current_lat=10.7750,
        current_lng=106.6950,
        is_online=True,
        is_busy=False,
    )
    prof_b = DriverProfile(
        user_id=user_b.id,
        license_plate="59-B1 2222",
        current_lat=10.9000,
        current_lng=106.8000,
        is_online=True,
        is_busy=False,
    )
    prof_c = DriverProfile(
        user_id=user_c.id,
        license_plate="59-C1 3333",
        current_lat=10.7720,
        current_lng=106.6920,
        is_online=True,
        is_busy=True,
    )
    prof_d = DriverProfile(
        user_id=user_d.id,
        license_plate="59-D1 4444",
        current_lat=10.7710,
        current_lng=106.6910,
        is_online=False,
        is_busy=False,
    )
    db_session.add_all([prof_a, prof_b, prof_c, prof_d])
    await db_session.commit()

    # 3. Gọi API GET /api/v1/driver/nearby bán kính 5km
    res = await client.get(
        f"/api/v1/driver/nearby?lat={center_lat}&lng={center_lng}&radius_km=5.0",
        headers=m_headers,
    )
    assert res.status_code == 200
    data = res.json()

    # Chỉ Driver A thỏa mãn (trong bán kính 5km, online và không bận)
    assert len(data) == 1
    assert data[0]["driver_id"] == prof_a.id
    assert data[0]["full_name"] == "Driver A (Gần)"
    assert data[0]["distance_km"] <= 1.5




@pytest.mark.anyio
async def test_auto_assign_nearest_driver_comprehensive(client: AsyncClient, db_session: AsyncSession):
    """
    Kiểm tra toàn diện flow tự động gán tài xế:
    1. Tạo Merchant & Nhà hàng tại (10.7700, 106.6900)
    2. Tạo Khách hàng & Đơn hàng READY_FOR_PICKUP
    3. Tạo 2 tài xế online:
       - Shipper Gần (10.7730, 106.6930) ~ 0.45 km
       - Shipper Xa (10.7900, 106.7100) ~ 3.1 km
    4. Kích hoạt POST /api/v1/driver/auto-assign/{order_id}
    5. Kiểm tra kết quả: Shipper Gần được chọn, trạng thái đơn đổi thành DRIVER_ASSIGNED, shipper is_busy=True
    6. Gọi lại lần 2 -> 409 Conflict (đơn đã có tài xế)
    """
    # 1. Đăng ký và login Merchant
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "owner_assign@foodhub.com",
            "password": "password123",
            "full_name": "Restaurant Owner",
            "role": "MERCHANT",
        },
    )
    m_token = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "owner_assign@foodhub.com", "password": "password123"},
        )
    ).json()["access_token"]
    m_headers = {"Authorization": f"Bearer {m_token}"}

    owner = (
        await db_session.execute(select(User).where(User.email == "owner_assign@foodhub.com"))
    ).scalar_one()

    # Tạo nhà hàng
    restaurant = Restaurant(
        owner_id=owner.id,
        name="Quan Com Ga Hoi An",
        address="123 Nguyen Trai, Q1",
        latitude=10.7700,
        longitude=106.6900,
        is_open=True,
    )
    db_session.add(restaurant)
    await db_session.commit()

    item = MenuItem(
        restaurant_id=restaurant.id,
        name="Com Ga Dac Biet",
        base_price=65000,
        stock_quantity=50,
        is_available=True,
    )
    db_session.add(item)
    await db_session.commit()

    # 2. Tạo Khách hàng và Đơn hàng ở trạng thái READY_FOR_PICKUP
    customer = User(
        email="customer_geo@foodhub.com",
        hashed_password="hash",
        full_name="Khach Hang Geo",
        role="CUSTOMER",
    )
    db_session.add(customer)
    await db_session.commit()

    order = Order(
        order_code="ORD-MATCH-001",
        customer_id=customer.id,
        restaurant_id=restaurant.id,
        delivery_address="456 Le Loi, Q1",
        delivery_lat=10.7750,
        delivery_lng=106.6980,
        subtotal=65000,
        delivery_fee=15000,
        discount_amount=0,
        total_amount=80000,
        status=OrderStatus.READY_FOR_PICKUP,
    )
    db_session.add(order)
    await db_session.commit()

    # 3. Tạo 2 tài xế
    u_near = User(
        email="shipper_near@foodhub.com",
        hashed_password="hash",
        full_name="Shipper Sieu Toc",
        phone="0902000001",
        role="DRIVER",
    )
    u_far = User(
        email="shipper_far@foodhub.com",
        hashed_password="hash",
        full_name="Shipper Duong Xa",
        phone="0902000002",
        role="DRIVER",
    )
    db_session.add_all([u_near, u_far])
    await db_session.commit()

    p_near = DriverProfile(
        user_id=u_near.id,
        license_plate="59-P1 9999",
        current_lat=10.7730,
        current_lng=106.6930,
        is_online=True,
        is_busy=False,
    )
    p_far = DriverProfile(
        user_id=u_far.id,
        license_plate="59-P2 8888",
        current_lat=10.7900,
        current_lng=106.7100,
        is_online=True,
        is_busy=False,
    )
    db_session.add_all([p_near, p_far])
    await db_session.commit()

    # 4. Merchant kích hoạt auto-assign
    assign_res = await client.post(
        f"/api/v1/driver/auto-assign/{order.id}",
        headers=m_headers,
    )
    assert assign_res.status_code == 200
    res_data = assign_res.json()

    # Phải chọn đúng Shipper Sieu Toc (gần hơn)
    assert res_data["driver_id"] == p_near.id
    assert res_data["driver_name"] == "Shipper Sieu Toc"
    assert res_data["status"] == OrderStatus.DRIVER_ASSIGNED.value
    assert res_data["distance_km"] < 1.0

    # 5. Kiểm tra DB
    await db_session.refresh(order)
    await db_session.refresh(p_near)
    assert order.status == OrderStatus.DRIVER_ASSIGNED
    assert p_near.is_busy is True

    # Kiểm tra DriverAssignment
    stmt_assign = select(DriverAssignment).where(DriverAssignment.order_id == order.id)
    assignment = (await db_session.execute(stmt_assign)).scalar_one()
    assert assignment.driver_id == p_near.id
    assert assignment.status == DriverAssignmentStatus.ACCEPTED

    # 6. Gọi lại lần 2 khi đơn đã được gán -> 409 Conflict
    conflict_res = await client.post(
        f"/api/v1/driver/auto-assign/{order.id}",
        headers=m_headers,
    )
    assert conflict_res.status_code == 409


@pytest.mark.anyio
async def test_auto_assign_no_driver_in_radius(client: AsyncClient, db_session: AsyncSession):
    """Kiểm tra trường hợp không có tài xế trong bán kính: trả về 404 Not Found."""
    # 1. Đăng ký Admin
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin_geo@foodhub.com",
            "password": "password123",
            "full_name": "System Admin",
            "role": "ADMIN",
        },
    )
    admin_token = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "admin_geo@foodhub.com", "password": "password123"},
        )
    ).json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 2. Tạo quán ở vị trí hẻo lánh (Núi rừng) và đơn hàng
    merchant = User(
        email="remote_merchant@foodhub.com",
        hashed_password="hash",
        full_name="Remote Merchant",
        role="MERCHANT",
    )
    customer = User(
        email="remote_customer@foodhub.com",
        hashed_password="hash",
        full_name="Remote Customer",
        role="CUSTOMER",
    )
    db_session.add_all([merchant, customer])
    await db_session.commit()

    remote_restaurant = Restaurant(
        owner_id=merchant.id,
        name="Quan Nuong Tren Nui",
        address="Dinh Nui Ba Den",
        latitude=11.3700,
        longitude=106.1700,
        is_open=True,
    )
    db_session.add(remote_restaurant)
    await db_session.commit()

    order = Order(
        order_code="ORD-MATCH-002",
        customer_id=customer.id,
        restaurant_id=remote_restaurant.id,
        delivery_address="Chan Nui",
        delivery_lat=11.3600,
        delivery_lng=106.1600,
        subtotal=100000,
        delivery_fee=15000,
        discount_amount=0,
        total_amount=115000,
        status=OrderStatus.READY_FOR_PICKUP,
    )
    db_session.add(order)
    await db_session.commit()

    # 3. Gọi auto-assign trong bán kính 5km -> 404 Not Found
    res = await client.post(
        f"/api/v1/driver/auto-assign/{order.id}?radius_km=5.0",
        headers=admin_headers,
    )
    assert res.status_code == 404
    assert "Không tìm thấy tài xế" in res.json()["detail"]


@pytest.mark.anyio
async def test_auto_assign_rbac_rejections(client: AsyncClient, db_session: AsyncSession):
    """Kiểm tra từ chối phân quyền: Customer bị 403, Merchant của quán khác bị 403."""
    # 1. Đăng ký Customer
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "unauthorized_customer@foodhub.com",
            "password": "password123",
            "full_name": "Troll Customer",
            "role": "CUSTOMER",
        },
    )
    c_token = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "unauthorized_customer@foodhub.com", "password": "password123"},
        )
    ).json()["access_token"]
    c_headers = {"Authorization": f"Bearer {c_token}"}

    # Customer gọi GET /nearby -> 403
    res_nearby = await client.get("/api/v1/driver/nearby?lat=10.77&lng=106.69", headers=c_headers)
    assert res_nearby.status_code == 403

    # Customer gọi POST /auto-assign -> 403
    res_assign = await client.post("/api/v1/driver/auto-assign/999", headers=c_headers)
    assert res_assign.status_code == 403
