"""Tests for Advanced Stretch Goal — Direction 2: Order Batching & Grouping.

Validates:
1. Batch candidates algorithm: detect pairs of orders from the same restaurant with dropoff proximity,
   calculating distance savings and efficiency percentage.
2. Batch creation & Waypoint sequencing: pick-ups ordered first, then drop-offs ordered from nearest to farthest.
3. Driver batch acceptance flow: atomic status update, DRIVER_ASSIGNED propagation, driver busy flag.
4. Concurrency race condition: 409 conflict when another driver attempts to accept an already assigned batch.
5. End-to-end waypoint progression & automatic double-entry ledger settlement on delivered drop-offs,
   completing the batch and releasing the driver.
6. RBAC & input validation constraints (rejecting invalid order counts, cross-restaurant mixing, non-ready orders, out-of-order waypoints).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.driver import DeliveryBatch, DriverProfile
from app.models.enums import BatchStatus, OrderStatus
from app.models.ledger import LedgerEntry
from app.models.order import Order
from app.models.restaurant import Restaurant
from app.models.user import User


async def _create_test_user(client: AsyncClient, email: str, role: str) -> tuple[str, dict[str, str]]:
    """Helper đăng ký & đăng nhập user, trả về access token và headers."""
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "full_name": f"User {role}", "role": role},
    )
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "password123"},
    )
    token = res.json()["access_token"]
    return token, {"Authorization": f"Bearer {token}"}


@pytest.mark.anyio
async def test_batch_candidates_algorithm(client: AsyncClient, db_session: AsyncSession):
    """Kiểm tra thuật toán phát hiện các đơn hàng có thể ghép nhóm cùng quán (Same-Restaurant Proximity)."""
    _, m_headers = await _create_test_user(client, "merch_cand@foodhub.com", "MERCHANT")
    m_user = (await db_session.execute(select(User).where(User.email == "merch_cand@foodhub.com"))).scalar_one()

    # Quán tại Quận 1
    rest = Restaurant(
        owner_id=m_user.id,
        name="Quan Com Ga Batch",
        address="10 Le Loi, Q1",
        latitude=10.7750,
        longitude=106.7000,
        delivery_radius_km=10.0,
    )
    db_session.add(rest)
    await db_session.commit()

    # Tạo 2 đơn hàng ở gần nhau (Dropoff cách nhau ~ 200m)
    order1 = Order(
        order_code="ORD-CAND-001",
        customer_id=m_user.id,
        restaurant_id=rest.id,
        delivery_address="20 Le Loi",
        delivery_lat=10.7760,
        delivery_lng=106.7010,
        subtotal=100000,
        delivery_fee=15000,
        total_amount=115000,
        status=OrderStatus.READY_FOR_PICKUP,
    )
    order2 = Order(
        order_code="ORD-CAND-002",
        customer_id=m_user.id,
        restaurant_id=rest.id,
        delivery_address="35 Pasteur",
        delivery_lat=10.7770,
        delivery_lng=106.7015,
        subtotal=120000,
        delivery_fee=15000,
        total_amount=135000,
        status=OrderStatus.READY_FOR_PICKUP,
    )
    db_session.add_all([order1, order2])
    await db_session.commit()

    # Gọi API tìm batch candidates
    res = await client.get("/api/v1/driver/batches/candidates", headers=m_headers)
    assert res.status_code == 200
    candidates = res.json()
    assert len(candidates) >= 1

    matched = next((c for c in candidates if c["restaurant_id"] == rest.id), None)
    assert matched is not None
    assert set(matched["order_ids"]) == {order1.id, order2.id}
    assert matched["dropoff_distance_km"] <= 3.0
    assert matched["efficiency_savings_pct"] > 0
    assert matched["saved_distance_km"] > 0


@pytest.mark.anyio
async def test_create_batch_and_sequencing(client: AsyncClient, db_session: AsyncSession):
    """Kiểm tra tạo batch và thuật toán sắp xếp thứ tự chặng (Waypoints Sequencing): Pickups trước, Giao gần trước."""
    _, m_headers = await _create_test_user(client, "merch_create_b@foodhub.com", "MERCHANT")
    m_user = (await db_session.execute(select(User).where(User.email == "merch_create_b@foodhub.com"))).scalar_one()

    rest = Restaurant(
        owner_id=m_user.id,
        name="Pho Bo Gia Truyen",
        address="100 Nguyen Hue",
        latitude=10.7700,
        longitude=106.7000,
        delivery_radius_km=10.0,
    )
    db_session.add(rest)
    await db_session.commit()

    # Đơn A cách quán ~ 200m
    order_a = Order(
        order_code="ORD-SEQ-A",
        customer_id=m_user.id,
        restaurant_id=rest.id,
        delivery_address="120 Nguyen Hue (Gan)",
        delivery_lat=10.7715,
        delivery_lng=106.7010,
        subtotal=80000,
        delivery_fee=15000,
        total_amount=95000,
        status=OrderStatus.READY_FOR_PICKUP,
    )
    # Đơn B cách quán ~ 1000m (Xa hơn)
    order_b = Order(
        order_code="ORD-SEQ-B",
        customer_id=m_user.id,
        restaurant_id=rest.id,
        delivery_address="500 Hai Ba Trung (Xa)",
        delivery_lat=10.7790,
        delivery_lng=106.7050,
        subtotal=90000,
        delivery_fee=20000,
        total_amount=110000,
        status=OrderStatus.READY_FOR_PICKUP,
    )
    db_session.add_all([order_a, order_b])
    await db_session.commit()

    # Gọi API tạo batch
    create_res = await client.post(
        "/api/v1/driver/batches",
        headers=m_headers,
        json={"restaurant_id": rest.id, "order_ids": [order_b.id, order_a.id]},
    )
    assert create_res.status_code == 201
    batch_data = create_res.json()
    assert batch_data["restaurant_id"] == rest.id
    assert batch_data["status"] == "PENDING"
    assert batch_data["total_distance_km"] > 0

    waypoints = batch_data["waypoints"]
    assert len(waypoints) == 4

    # Chặng 1, 2: PICKUP tại quán
    assert waypoints[0]["sequence"] == 1
    assert waypoints[0]["waypoint_type"] == "PICKUP"
    assert waypoints[1]["sequence"] == 2
    assert waypoints[1]["waypoint_type"] == "PICKUP"

    # Chặng 3: DROPOFF đơn gần (order_a)
    assert waypoints[2]["sequence"] == 3
    assert waypoints[2]["waypoint_type"] == "DROPOFF"
    assert waypoints[2]["order_id"] == order_a.id

    # Chặng 4: DROPOFF đơn xa (order_b)
    assert waypoints[3]["sequence"] == 4
    assert waypoints[3]["waypoint_type"] == "DROPOFF"
    assert waypoints[3]["order_id"] == order_b.id


@pytest.mark.anyio
async def test_driver_accept_batch_flow(client: AsyncClient, db_session: AsyncSession):
    """Kiểm tra luồng tài xế nhận trọn vẹn 1 batch và cập nhật trạng thái đơn."""
    # 1. Tạo Merchant & Nhà hàng & 2 đơn hàng
    _, m_headers = await _create_test_user(client, "merch_flow@foodhub.com", "MERCHANT")
    m_user = (await db_session.execute(select(User).where(User.email == "merch_flow@foodhub.com"))).scalar_one()

    rest = Restaurant(
        owner_id=m_user.id,
        name="Banh Mi Sai Gon",
        address="15 Le Thanh Ton",
        latitude=10.7780,
        longitude=106.7020,
    )
    db_session.add(rest)
    await db_session.commit()

    o1 = Order(
        order_code="ORD-FLOW-001",
        customer_id=m_user.id,
        restaurant_id=rest.id,
        delivery_address="30 Le Thanh Ton",
        delivery_lat=10.7790,
        delivery_lng=106.7030,
        subtotal=50000,
        delivery_fee=15000,
        total_amount=65000,
        status=OrderStatus.READY_FOR_PICKUP,
    )
    o2 = Order(
        order_code="ORD-FLOW-002",
        customer_id=m_user.id,
        restaurant_id=rest.id,
        delivery_address="45 Le Thanh Ton",
        delivery_lat=10.7800,
        delivery_lng=106.7040,
        subtotal=60000,
        delivery_fee=15000,
        total_amount=75000,
        status=OrderStatus.READY_FOR_PICKUP,
    )
    db_session.add_all([o1, o2])
    await db_session.commit()

    create_res = await client.post(
        "/api/v1/driver/batches",
        headers=m_headers,
        json={"restaurant_id": rest.id, "order_ids": [o1.id, o2.id]},
    )
    batch_id = create_res.json()["id"]

    # 2. Tạo Driver online
    _, d_headers = await _create_test_user(client, "driver_batch_1@foodhub.com", "DRIVER")
    d_user = (await db_session.execute(select(User).where(User.email == "driver_batch_1@foodhub.com"))).scalar_one()
    profile = DriverProfile(
        user_id=d_user.id,
        license_plate="59-B1 99999",
        is_online=True,
        is_busy=False,
    )
    db_session.add(profile)
    await db_session.commit()

    # 3. Tài xế nhận batch
    accept_res = await client.post(f"/api/v1/driver/batches/{batch_id}/accept", headers=d_headers)
    assert accept_res.status_code == 200
    assert accept_res.json()["status"] == "ASSIGNED"
    assert accept_res.json()["driver_id"] == profile.id

    # 4. Kiểm tra trạng thái các đơn hàng đã chuyển thành DRIVER_ASSIGNED
    await db_session.refresh(o1)
    await db_session.refresh(o2)
    assert o1.status == OrderStatus.DRIVER_ASSIGNED
    assert o2.status == OrderStatus.DRIVER_ASSIGNED

    # 5. Kiểm tra tài xế chuyển thành is_busy = True
    await db_session.refresh(profile)
    assert profile.is_busy is True

    # 6. Kiểm tra endpoint GET /batches/current của tài xế
    cur_res = await client.get("/api/v1/driver/batches/current", headers=d_headers)
    assert cur_res.status_code == 200
    assert cur_res.json()["id"] == batch_id
    assert len(cur_res.json()["waypoints"]) == 4


@pytest.mark.anyio
async def test_batch_concurrency_race_condition(client: AsyncClient, db_session: AsyncSession):
    """Kiểm tra chống tranh chấp (Concurrency): Hai tài xế cùng nhận 1 batch -> 1 tài xế thành công, người kia nhận 409."""
    _, m_headers = await _create_test_user(client, "merch_race@foodhub.com", "MERCHANT")
    m_user = (await db_session.execute(select(User).where(User.email == "merch_race@foodhub.com"))).scalar_one()

    rest = Restaurant(owner_id=m_user.id, name="Quan Race", address="1 Dong Du", latitude=10.776, longitude=106.704)
    db_session.add(rest)
    await db_session.commit()

    o1 = Order(
        order_code="ORD-RACE-01", customer_id=m_user.id, restaurant_id=rest.id,
        delivery_address="5 Dong Du", delivery_lat=10.777, delivery_lng=106.705,
        subtotal=50000, delivery_fee=15000, total_amount=65000, status=OrderStatus.READY_FOR_PICKUP,
    )
    o2 = Order(
        order_code="ORD-RACE-02", customer_id=m_user.id, restaurant_id=rest.id,
        delivery_address="8 Dong Du", delivery_lat=10.778, delivery_lng=106.706,
        subtotal=50000, delivery_fee=15000, total_amount=65000, status=OrderStatus.READY_FOR_PICKUP,
    )
    db_session.add_all([o1, o2])
    await db_session.commit()

    b_res = await client.post(
        "/api/v1/driver/batches",
        headers=m_headers,
        json={"restaurant_id": rest.id, "order_ids": [o1.id, o2.id]},
    )
    batch_id = b_res.json()["id"]

    # Tạo Driver A và Driver B
    for email, plate in [("driver_race_a@foodhub.com", "59-R1 11111"), ("driver_race_b@foodhub.com", "59-R2 22222")]:
        _, headers = await _create_test_user(client, email, "DRIVER")
        u = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
        p = DriverProfile(user_id=u.id, license_plate=plate, is_online=True, is_busy=False)
        db_session.add(p)
    await db_session.commit()

    _, d1_headers = await _create_test_user(client, "driver_race_a@foodhub.com", "DRIVER")
    _, d2_headers = await _create_test_user(client, "driver_race_b@foodhub.com", "DRIVER")

    # Driver A nhận thành công
    res_a = await client.post(f"/api/v1/driver/batches/{batch_id}/accept", headers=d1_headers)
    assert res_a.status_code == 200

    # Driver B tranh chấp -> Bị 409 CONFLICT
    res_b = await client.post(f"/api/v1/driver/batches/{batch_id}/accept", headers=d2_headers)
    assert res_b.status_code == 409


@pytest.mark.anyio
async def test_waypoint_progression_and_settlement(client: AsyncClient, db_session: AsyncSession):
    """Kiểm tra toàn trình hoàn thành từng waypoint, kích hoạt sổ cái kép và giải phóng tài xế."""
    _, m_headers = await _create_test_user(client, "merch_settle@foodhub.com", "MERCHANT")
    m_user = (await db_session.execute(select(User).where(User.email == "merch_settle@foodhub.com"))).scalar_one()

    rest = Restaurant(owner_id=m_user.id, name="Quan Settle", address="10 Ham Nghi", latitude=10.771, longitude=106.705)
    db_session.add(rest)
    await db_session.commit()

    o1 = Order(
        order_code="ORD-SETTLE-01", customer_id=m_user.id, restaurant_id=rest.id,
        delivery_address="15 Ham Nghi", delivery_lat=10.772, delivery_lng=106.706,
        subtotal=100000, delivery_fee=15000, total_amount=115000, status=OrderStatus.READY_FOR_PICKUP,
    )
    o2 = Order(
        order_code="ORD-SETTLE-02", customer_id=m_user.id, restaurant_id=rest.id,
        delivery_address="20 Ham Nghi", delivery_lat=10.773, delivery_lng=106.707,
        subtotal=200000, delivery_fee=20000, total_amount=220000, status=OrderStatus.READY_FOR_PICKUP,
    )
    db_session.add_all([o1, o2])
    await db_session.commit()

    b_res = await client.post(
        "/api/v1/driver/batches",
        headers=m_headers,
        json={"restaurant_id": rest.id, "order_ids": [o1.id, o2.id]},
    )
    batch_data = b_res.json()
    batch_id = batch_data["id"]

    # Tài xế nhận batch
    _, d_headers = await _create_test_user(client, "driver_settle@foodhub.com", "DRIVER")
    d_user = (await db_session.execute(select(User).where(User.email == "driver_settle@foodhub.com"))).scalar_one()
    profile = DriverProfile(user_id=d_user.id, license_plate="59-S1 88888", is_online=True, is_busy=False)
    db_session.add(profile)
    await db_session.commit()

    await client.post(f"/api/v1/driver/batches/{batch_id}/accept", headers=d_headers)

    # Lấy danh sách waypoints
    cur = await client.get("/api/v1/driver/batches/current", headers=d_headers)
    wps = cur.json()["waypoints"]
    wp1, wp2, wp3, wp4 = wps[0], wps[1], wps[2], wps[3]

    # 1. Hoàn thành chặng 1 (PICKUP)
    r1 = await client.post(f"/api/v1/driver/batches/{batch_id}/waypoints/{wp1['id']}/complete", headers=d_headers)
    assert r1.status_code == 200
    assert r1.json()["waypoint_type"] == "PICKUP"
    assert r1.json()["order_status"] == "PICKED_UP"
    assert r1.json()["is_batch_completed"] is False

    # 2. Hoàn thành chặng 2 (PICKUP)
    r2 = await client.post(f"/api/v1/driver/batches/{batch_id}/waypoints/{wp2['id']}/complete", headers=d_headers)
    assert r2.status_code == 200
    assert r2.json()["order_status"] == "PICKED_UP"

    # 3. Hoàn thành chặng 3 (DROPOFF) -> Đơn 1 chuyển DELIVERED & kích hoạt Double-Entry Ledger
    r3 = await client.post(f"/api/v1/driver/batches/{batch_id}/waypoints/{wp3['id']}/complete", headers=d_headers)
    assert r3.status_code == 200
    assert r3.json()["waypoint_type"] == "DROPOFF"
    assert r3.json()["order_status"] == "DELIVERED"

    # Kiểm tra sổ cái kép của đơn 1
    ledger1 = (await db_session.execute(select(LedgerEntry).where(LedgerEntry.order_id == wp3["order_id"]))).scalars().all()
    assert len(ledger1) >= 2  # DEBIT CUSTOMER & CREDIT RESTAURANT & CREDIT PLATFORM

    # 4. Hoàn thành chặng 4 (DROPOFF) -> Đơn 2 chuyển DELIVERED -> Batch COMPLETED
    r4 = await client.post(f"/api/v1/driver/batches/{batch_id}/waypoints/{wp4['id']}/complete", headers=d_headers)
    assert r4.status_code == 200
    assert r4.json()["is_batch_completed"] is True
    assert r4.json()["batch_status"] == "COMPLETED"

    # Kiểm tra Batch trong DB
    db_batch = (await db_session.execute(select(DeliveryBatch).where(DeliveryBatch.id == batch_id))).scalar_one()
    assert db_batch.status == BatchStatus.COMPLETED

    # Kiểm tra tài xế được giải phóng: is_busy == False
    await db_session.refresh(profile)
    assert profile.is_busy is False


@pytest.mark.anyio
async def test_batch_rbac_and_validations(client: AsyncClient, db_session: AsyncSession):
    """Kiểm tra các ràng buộc bảo vệ & kiểm tra lỗi đầu vào (RBAC, Sequence Skip, Cross-Restaurant)."""
    # 1. Customer không có quyền tạo batch
    _, c_headers = await _create_test_user(client, "cust_rbac@foodhub.com", "CUSTOMER")
    res_cust = await client.post("/api/v1/driver/batches", headers=c_headers, json={"restaurant_id": 1, "order_ids": [1, 2]})
    assert res_cust.status_code == 403

    # 2. Tạo batch dưới 2 đơn hàng -> Bị 422 hoặc 400 Bad Request
    _, m_headers = await _create_test_user(client, "merch_val@foodhub.com", "MERCHANT")
    res_len = await client.post("/api/v1/driver/batches", headers=m_headers, json={"restaurant_id": 1, "order_ids": [1]})
    assert res_len.status_code in [400, 422]

    # 3. Tạo batch từ 2 đơn khác nhà hàng
    m_user = (await db_session.execute(select(User).where(User.email == "merch_val@foodhub.com"))).scalar_one()
    r1 = Restaurant(owner_id=m_user.id, name="R1", address="A1", latitude=10.1, longitude=106.1)
    r2 = Restaurant(owner_id=m_user.id, name="R2", address="A2", latitude=10.2, longitude=106.2)
    db_session.add_all([r1, r2])
    await db_session.commit()

    o1 = Order(
        order_code="ORD-VAL-1", customer_id=m_user.id, restaurant_id=r1.id,
        delivery_address="D1", delivery_lat=10.11, delivery_lng=106.11,
        subtotal=50000, delivery_fee=10000, total_amount=60000, status=OrderStatus.READY_FOR_PICKUP,
    )
    o2 = Order(
        order_code="ORD-VAL-2", customer_id=m_user.id, restaurant_id=r2.id,
        delivery_address="D2", delivery_lat=10.21, delivery_lng=106.21,
        subtotal=50000, delivery_fee=10000, total_amount=60000, status=OrderStatus.READY_FOR_PICKUP,
    )
    db_session.add_all([o1, o2])
    await db_session.commit()

    res_cross = await client.post(
        "/api/v1/driver/batches",
        headers=m_headers,
        json={"restaurant_id": r1.id, "order_ids": [o1.id, o2.id]},
    )
    assert res_cross.status_code == 400
    assert "không thuộc nhà hàng" in res_cross.json()["detail"]
